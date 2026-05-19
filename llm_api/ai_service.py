import os
import gc
import json
import requests
import re
from django.conf import settings
from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline, BitsAndBytesConfig, GenerationConfig
import outlines

import torch
import spacy
from pydantic import BaseModel
import typing
import threading
nlp = spacy.blank("en")
nlp.add_pipe("sentencizer")

if torch.cuda.is_available():
    print("✅ Success! PyTorch can see the GPU.")
    device_count = torch.cuda.device_count()
    print(f"CUDA Devices Available: {device_count}")
    for i in range(device_count):
        print(f"Device {i}: {torch.cuda.get_device_name(i)}")
    # This shows the CUDA version PyTorch was built with
    print(f"PyTorch was built with CUDA version: {torch.version.cuda}")
else:
    print("❌ Failure. PyTorch cannot see the GPU.")


class AIService:
    """A singleton to hold all our AI models and data."""
    class DefaultOutlineResponse(BaseModel):
        response_content: str

    role = getattr(settings, "VERBAL_ROLE", "standalone")
    inference_url = getattr(settings, "INFERENCE_URL", "http://127.0.0.1:8001/api/llm")
    model_id = None
    model = None
    classifier = None
    llm_pipeline = None
    outline_pipeline = None
    terminators = None
    embedding_model = None
    tokenizer = None
    default_outline_response = DefaultOutlineResponse
    _generator_cache = {}
    _lock = threading.RLock()  # "Reentrant thread lock"



    def load_models(self):
        token = os.getenv("HF_TOKEN")

        self.model_id = None
        tokenizer_id = "Qwen/Qwen2.5-3B-Instruct"
        
        try:
            from .models import SystemConfiguration
            config = SystemConfiguration.get_solo()
            tokenizer_id = config.system_tokenizer_id
            
            if config.active_local_model:
                self.model_id = config.active_local_model.hf_model_id
                print(f"📂 System Config requests PyTorch load for: {self.model_id}")
        except Exception as e:
            print(f"Warning: Could not fetch SystemConfiguration from DB: {e}")

        print(f"⚙️ Loading CPU tokenizer: {tokenizer_id}")
        self.tokenizer = AutoTokenizer.from_pretrained(tokenizer_id, token=token)
        # Load your main LLM
        if self.tokenizer.pad_token_id is None:
            self.tokenizer.pad_token_id = self.tokenizer.eos_token_id
        if self.role in ["web", "worker"]:
            print(f"💻 Running in proxy mode (Role: {self.role}). Tokenizer loaded, bypassing heavy LLM load.")
            
            # Verify HTTP connection
            try:
                ping_url = f"{self.inference_url.rstrip('/')}/internal/ping/"
                res = requests.get(ping_url, timeout=3.0)
                res.raise_for_status()
                print("✅ Successfully connected to inference server.")
            except requests.exceptions.RequestException as e:
                print(f"⚠️ WARNING: Cannot connect to inference server at {self.inference_url}. Ensure it is running! Error: {e}")
            return

        if not self.model_id:
            print("🛑 No Local AI Model selected in System Configuration. Bypassing PyTorch to save VRAM.")
            return

        print("🚀 Loading Heavy AI models into VRAM...")
        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.float16
        )

        # Ensure the model knows this too
        self.model = AutoModelForCausalLM.from_pretrained(
            self.model_id,
            # dtype=torch.float16,
            device_map={"": 0},
            quantization_config=quantization_config,
            low_cpu_mem_usage=True,
            token=token
        )
        self.model.config.pad_token_id = self.tokenizer.eos_token_id

        self.llm_pipeline = pipeline(
            "text-generation",
            model=self.model,
            tokenizer=self.tokenizer,
            # Add your model loading params here (quantization, etc.)
        )
        print("✅ LLM pipeline loaded successfully.", type(self.llm_pipeline))
        self.outline_pipeline = outlines.from_transformers(self.model, self.tokenizer)
        print("✅ Outline llm wrapper loaded", type(self.outline_pipeline))

        terminators = [
            self.tokenizer.eos_token_id,
            self.tokenizer.convert_tokens_to_ids("<|eot_id|>")
        ]
        self.terminators = [t for t in terminators if t is not None]

        print("✅ AI models loaded successfully.")

    def unload_models(self):
        """Frees VRAM for model switching."""
        print("🗑️ Unloading AI models...")
        del self.model
        del self.llm_pipeline
        del self.outline_pipeline
        self._generator_cache.clear()
        self.model = None
        self.llm_pipeline = None
        self.outline_pipeline = None
        
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        print("✅ VRAM cleared.")

    def _extract_prompts(self, messages):
        system_prompt = ""
        user_prompt = ""
        if isinstance(messages, str):
            user_prompt = messages
        elif isinstance(messages, list):
            for m in messages:
                if m.get("role") == "system" and not system_prompt:
                    system_prompt = m.get("content", "")
                elif m.get("role") == "user":
                    user_prompt = m.get("content", "")
        return system_prompt, user_prompt

    def _log_generation(self, messages, generated_texts, log_kwargs=None):
        if log_kwargs is None:
            log_kwargs = {}
        if log_kwargs.get("skip_log"):
            return

        system_prompt, user_prompt = self._extract_prompts(messages)
        system_prompt = log_kwargs.get("system_prompt", system_prompt)
        user_prompt = log_kwargs.get("user_prompt", user_prompt)

        try:
            from .models import PromptResponseLog  # Lazy import avoids circular dependency
            for text in generated_texts:
                text_to_save = text.model_dump_json(indent=2) if hasattr(text, 'model_dump_json') else str(text)
                PromptResponseLog.objects.create(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    generated_response=text_to_save,
                    user_id=log_kwargs.get("user_id"),
                    conversation_id=log_kwargs.get("conversation_id"),
                    rag_selections=log_kwargs.get("rag_selections", "")
                )
        except Exception as e:
            print(f"Warning: Failed to log prompt response: {e}")

    def _execute_openai_standard_request(self, messages, max_new_tokens, temperature, num_return_sequences, response_schema=None, user=None):
        """Unifies HTTP calls to either external (OpenAI) or internal proxy endpoints."""
        api_url = f"{self.inference_url.rstrip('/')}/v1/chat/completions"
        api_key = None
        target_model = "local-model"
        
        # 1. Inspect User Settings
        if user and not getattr(user, 'is_anonymous', False):
            try:
                from .models import UserActiveModel, UserAPIKey
                prefs = UserActiveModel.objects.get(user=user)
                if prefs.use_external and prefs.active_external:
                    api_url = prefs.active_external.api_url
                    target_model = prefs.active_external.api_model_name
                    key_obj = UserAPIKey.objects.filter(user=user, provider=prefs.active_external.provider).first()
                    if key_obj:
                        api_key = key_obj.api_key
            except Exception:
                pass

        # 2. Build standard payload
        payload = {
            "model": target_model,
            "messages": messages if isinstance(messages, list) else [{"role": "user", "content": messages}],
            "temperature": temperature,
            "max_tokens": max_new_tokens,
            "n": num_return_sequences
        }

        if response_schema:
            schema_json = None
            if hasattr(response_schema, "model_json_schema"):
                schema_json = response_schema.model_json_schema()
            elif isinstance(response_schema, dict):
                schema_json = response_schema
            
            if schema_json:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": getattr(response_schema, "__name__", "json_schema"),
                        "schema": schema_json,
                        "strict": True
                    }
                }

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            response = requests.post(api_url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            
            # Parse OpenAI standard response
            results = [choice["message"]["content"] for choice in data.get("choices", [])]
            
            # If we are the background worker, pause for a moment so Web UI requests can jump the queue!
            if self.role == "worker":
                import time
                time.sleep(0.5)
                
            return results if num_return_sequences > 1 else results[0]

        except requests.exceptions.RequestException as e:
            raise ConnectionError(f"Failed to communicate with API at {api_url}: {e}")

    def _get_schema_cache_key(self, response_schema):
        """Helper to create a deterministic cache key for outline schemas."""
        if response_schema is None:
            return "default_outline_response"
        if hasattr(response_schema, "model_json_schema"):
            return response_schema.__name__
        if isinstance(response_schema, dict):
            return json.dumps(response_schema, sort_keys=True)
        return str(response_schema)

    def _perform_active_rag_search(self, term, searched_terms, working_messages):
        """
        Searches the Grips Knowledge Graph and standard RAG for a term, 
        injecting the results back into the working_messages.
        Returns True if a new search was performed, False if already searched.
        """
        if term in searched_terms:
            print(f"⚠️ Active RAG: Already searched for '{term}'. Ignoring to prevent infinite loop.")
            return False
            
        print(f"🔍 Active RAG Triggered: Halting validation to search Knowledge Graph and RAG for '{term}'...")
        searched_terms.add(term)
        
        from llm_api.apps import service_registry
        
        context_parts = []
        
        # 1. Search Grips Knowledge Graph
        grips_service = service_registry.grips_service
        if grips_service:
            try:
                grips_docs = grips_service.get_grips_context(term, k=3)
                if grips_docs:
                    context_parts.append("\n\n".join([f"Concept [{d.metadata.get('title', 'Unknown')}]:\n{d.page_content}" for d in grips_docs]))
            except Exception as e:
                print(f"Grips search failed: {e}")
                
        # 2. Search Standard RAG
        rag_service = service_registry.rag_service
        if rag_service:
            try:
                rag_docs = rag_service.get_context(term, k=3)
                if rag_docs:
                    context_parts.append("\n\n".join([f"Source: {d.metadata.get('filename', 'Unknown')}\nContent: {d.page_content}" for d in rag_docs]))
            except Exception as e:
                print(f"RAG search failed: {e}")
                
        context_str = "\n\n---\n\n".join(context_parts) if context_parts else "No specific concepts found."
        
        # Inject the result and prime the model to continue walking the graph
        injection = f"\n\n[System Search Result for '{term}':\n{context_str}\nNote: You may use <SEARCH: new_term> to explore further.]\n"
        
        # Append safely to the last user message to preserve chat template structure
        for i in range(len(working_messages)-1, -1, -1):
            if working_messages[i]["role"] == "user":
                working_messages[i] = working_messages[i].copy()
                working_messages[i]["content"] += injection
                break
                
        return True

    def _get_valid_structured_result(self, raw_results, response_schema):
        """Attempts to validate a list of results against a schema, returning the first valid one."""
        last_error = None
        for res in raw_results:
            try:
                # Verify validity but discard the constructed object
                if response_schema and hasattr(response_schema, "model_validate"):
                    if isinstance(res, str):
                        response_schema.model_validate_json(res)
                    else:
                        response_schema.model_validate(res)
                elif isinstance(response_schema, dict) or response_schema is None:
                    if isinstance(res, str) and response_schema is not None:
                        json.loads(res)
                        
                # Success!
                return res
            except Exception as ve:
                last_error = ve
                continue
                
        raise ValueError(f"All generated sequences failed validation. Last error: {last_error}")

    def _execute_generation_with_retries(self, generate_callable, messages, response_schema, max_new_tokens, temperature, num_return_sequences, log_kwargs, is_structured):
        """
        Universal executor handling Validation, Retries, Multiples, and Active RAG (Self-Guidance).
        Works identically for local/proxy and structured/unstructured generations.
        """
        working_messages = list(messages) if isinstance(messages, list) else [{"role": "user", "content": messages}]
        max_search_hops = 4
        max_validation_attempts = 2
        searched_terms = set()
        
        for search_hop in range(max_search_hops):
            did_search = False
            
            for attempt in range(max_validation_attempts):
                current_n = num_return_sequences if attempt == 0 else max(2, num_return_sequences + 1)
                
                try:
                    raw_results = generate_callable(working_messages, max_new_tokens, temperature, current_n)
                    last_error = None
                    
                    for res in raw_results:
                        # 1. Active RAG Check: Look for <SEARCH: concept> anywhere in output
                        search_match = re.search(r'<SEARCH:\s*([^>]+)>', str(res))
                        if search_match:
                            term = search_match.group(1).strip()
                            did_search = self._perform_active_rag_search(term, searched_terms, working_messages)
                            if did_search:
                                break  # Break out of result inspection to restart generation with new context!
                    
                    if did_search:
                        break  # Break attempt loop, proceed to next search_hop
                        
                    # 2. Return unstructured immediately if no search was triggered
                    if not is_structured:
                        self._log_generation(working_messages, raw_results, log_kwargs)
                        return raw_results
                        
                    # 3. Validation Check for structured outputs
                    valid_res = self._get_valid_structured_result(raw_results, response_schema)
                    
                    # Success! We log working_messages so the DB reflects the Active RAG injections
                    self._log_generation(working_messages, [valid_res], log_kwargs)
                    return valid_res
                    
                except Exception as e:
                    if attempt == max_validation_attempts - 1 and not did_search:
                        print(f"Error in generation (Attempt {attempt+1}/{max_validation_attempts}): {e}")
                        if is_structured:
                            return {"error": "GenerationFailed", "details": str(e)}
                        else:
                            return [f"GenerationFailed: {str(e)}"]
                            
        # Exhausted all search hops
        if is_structured:
            return {"error": "GenerationFailed", "details": "Exhausted Active RAG search hops."}
        else:
            return ["GenerationFailed: Exhausted Active RAG search hops."]

    def generate_response(self, messages, max_new_tokens=1024, num_return_sequences=1, temperature=0.7, log_kwargs=None, user=None):
        """Facade for standard chat completions."""
        needs_proxy = self.role in ["web", "worker"]
        if user and not getattr(user, 'is_anonymous', False):
            from .models import UserActiveModel
            if UserActiveModel.objects.filter(user=user, use_external=True).exists():
                needs_proxy = True

        if needs_proxy:
            def proxy_callable(msgs, max_tok, temp, n):
                res = self._execute_openai_standard_request(msgs, max_tok, temp, n, None, user=user)
                return res if isinstance(res, list) else [res]
            generate_callable = proxy_callable
        else:
            def local_callable(msgs, max_tok, temp, n):
                with self._lock:
                    if self.llm_pipeline is None:
                        self.load_models()
                    if self.llm_pipeline is None:
                        raise RuntimeError("No Local AI Model is active in System Configuration, and no External API is configured for this user.")
                    msgs_summary = self.summarize_conversation(msgs)
                    
                    import copy
                    gen_config = copy.deepcopy(self.model.generation_config)
                    gen_config.do_sample = True
                    gen_config.top_p = 0.95
                    gen_config.temperature = temp
                    gen_config.max_new_tokens = max_tok
                    gen_config.eos_token_id = self.terminators
                    gen_config.num_return_sequences = n
                    gen_config.max_length = None  # Suppresses the max_length precedence warning
                    
                    res = self.llm_pipeline(
                        msgs_summary, generation_config=gen_config, return_full_text=False
                    )
                    return [r['generated_text'] for r in res]
            generate_callable = local_callable

        return self._execute_generation_with_retries(
            generate_callable, messages, None, max_new_tokens, temperature, 
            num_return_sequences, log_kwargs, is_structured=False
        )

    def generate_outline(self, messages,
                         response_schema=None,
                         max_new_tokens=500,
                         temperature=0.7,
                         num_return_sequences=1,
                         log_kwargs=None,
                         user=None):
        """Facade that routes generating requests between HTTP proxy and Local VRAM execution."""

        needs_proxy = self.role in ["web", "worker"]
        if user and not getattr(user, 'is_anonymous', False):
            from .models import UserActiveModel
            if UserActiveModel.objects.filter(user=user, use_external=True).exists():
                needs_proxy = True

        if needs_proxy:
            def proxy_callable(msgs, max_tok, temp, n):
                res = self._execute_openai_standard_request(msgs, max_tok, temp, n, response_schema, user=user)
                return res if isinstance(res, list) else [res]
            generate_callable = proxy_callable
        else:
            with self._lock:
                if self.outline_pipeline is None:
                    self.load_models()
                    
                cache_key = self._get_schema_cache_key(response_schema)
                if cache_key not in self._generator_cache:
                    actual_schema = response_schema or self.default_outline_response
                    if isinstance(actual_schema, dict):
                        try:
                            from outlines.types import JsonSchema
                            try: actual_schema = JsonSchema(json.dumps(actual_schema))
                            except Exception: actual_schema = JsonSchema(actual_schema)
                        except ImportError: pass
                    self._generator_cache[cache_key] = outlines.Generator(self.outline_pipeline, actual_schema)
                generator = self._generator_cache[cache_key]
                
            def local_callable(msgs, max_tok, temp, n):
                prompt = self.tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True) if isinstance(msgs, list) else msgs
                kwargs = {"do_sample": True, "max_new_tokens": max_tok, "temperature": temp, "max_length": None}
                if n > 1: kwargs["num_return_sequences"] = n
                with self._lock:
                    if self.outline_pipeline is None:
                        raise RuntimeError("No Local AI Model is active in System Configuration, and no External API is configured for this user.")
                    res = generator(prompt, **kwargs)
                return res if isinstance(res, list) else [res]
            generate_callable = local_callable

        return self._execute_generation_with_retries(
            generate_callable, messages, response_schema, max_new_tokens, 
            temperature, num_return_sequences, log_kwargs, is_structured=True
        )

    def label_single(self, text, labels):
        # lazy load
        if self.classifier is None:
            try:
                self.classifier = pipeline(
                    "zero-shot-classification",
                    model="facebook/bart-large-mnli",
                    device=0
                )
            except Exception:
                print("GPU not found, falling back to CPU. This will be slow.")
                self.classifier = pipeline(
                    "zero-shot-classification",
                    model="facebook/bart-large-mnli",
                    device=-1  # Use -1 for CPU
                )


    def clean_response(self, response_content):
        assistant_response = str(response_content).strip()
        # Globally remove known special tokens that may leak into generations
        assistant_response = re.sub(r'<\|eot_id\|>|<eos>|<turn\|>|<\/s>', '', assistant_response, flags=re.IGNORECASE).strip()
        
        print("Assistant response", assistant_response)
        doc = nlp(assistant_response)
        sentences = list(doc.sents)  # Convert the generator to a list

        if not sentences:
            return ""

        # Get the last detected sentence
        last_sentence = sentences[-1]

        # Check the very last token of the last sentence
        # If it's not punctuation, assume the sentence is a fragment.
        if not last_sentence[-1].is_punct and len(sentences) > 1:
            # Return all sentences except the last one
            return " ".join([sent.text.strip() for sent in sentences[:-1]])
        else:
            # All detected sentences seem complete
            return " ".join([sent.text.strip() for sent in sentences])

    def count_conversation_tokens(self, messages: list) ->int:
        if not self.tokenizer:
            self.load_models()

        try:
            # apply_chat_template is the only way to be 100% accurate
            # as it includes all special role tokens.
            token_list = self.tokenizer.apply_chat_template(
                messages,
                add_generation_prompt=True
            )
            return len(token_list)
        except NotImplementedError:
            # A fallback for older models without a chat template.
            # This is a *rough estimate* and will be inaccurate.
            print("Warning: No chat template; falling back to naive token count.")
            total_tokens = 0
            for msg in messages:
                # This misses role tokens, so it will under-count.
                total_tokens += len(self.tokenizer.encode(msg['content']))
            return total_tokens

    def summarize_conversation(self, messages: list) -> str:
        total_tokens = self.count_conversation_tokens(messages)
        if total_tokens < 4000:
            return messages
        else:
            system_prompt = messages[0]
            summarize_these = messages[1:(len(messages)-1)//2] # for 13 msgs, get 1-6 for summarisation
            summarization_instruction = [{"role": "system", "content": "Summarize this following conversation to ensure the assistant remembers the most important aspects of the user's requests."}]
            # Unpack the list returned by generate_response
            [summary_message] = self.generate_response(summarization_instruction + summarize_these, max_new_tokens=400)
            summary_message = self.clean_response(summary_message)

            system_prompt['content'] = system_prompt['content'] + "Summary:\n  " + summary_message + "\n"
            new_messages = [system_prompt] + messages[((len(messages)-1)//2) - 1:]
            return new_messages