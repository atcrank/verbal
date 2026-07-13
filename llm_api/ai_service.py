import logging
logger = logging.getLogger(__name__)

import os
import gc
import json
import requests
import re
from django.conf import settings

import torch
# Hotfix for transformers/torch compatibility bug with missing fp8 dtypes
if not hasattr(torch, "float8_e8m0fnu"):
    setattr(torch, "float8_e8m0fnu", None)

from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline, BitsAndBytesConfig, GenerationConfig
import outlines
from outlines import models as outline_models

import spacy
from pydantic import BaseModel
import typing
import threading
nlp = spacy.blank("en")
nlp.add_pipe("sentencizer")

if torch.cuda.is_available():
    logger.info('✅ Success! PyTorch can see the GPU.')
    device_count = torch.cuda.device_count()

    # Get the compute capability of the primary GPU (device 0)
    major, minor = torch.cuda.get_device_capability(0)

    # Turing architecture (like 1660 Ti) is 7.5. Ampere (30-series) and newer are 8.0+.
    # Flash Attention 2 is only well-supported on Compute Capability 8.0 and higher.
    if major < 8:
        logger.info(f'⚠️ GPU with old compute capability ({major}.{minor}) detected. Disabling Flash Attention for compatibility.')
        torch.backends.cuda.enable_flash_sdp(False)
        torch.backends.cuda.enable_mem_efficient_sdp(False)
        torch.backends.cuda.enable_math_sdp(True)  # Use the math kernel as a fallback

    logger.info(f'CUDA Devices Available: {device_count}')
    for i in range(device_count):
        logger.info(f'Device {i}: {torch.cuda.get_device_name(i)}')
    # This shows the CUDA version PyTorch was built with
    logger.info(f'PyTorch was built with CUDA version: {torch.version.cuda}')
else:
    logger.info('❌ Failure. PyTorch cannot see the GPU.')


class AIService:
    """A singleton to hold all our AI models and data."""
    class DefaultOutlineResponse(BaseModel):
        response_content: str

    role = getattr(settings, "VERBAL_ROLE", "standalone")
    inference_url = getattr(settings, "INFERENCE_URL", "http://127.0.0.1:8001/api/llm")
    model_id = None
    model = None
    classifier = None
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
            if config:
                tokenizer_id = config.system_tokenizer_id
                
                if config.active_local_model:
                    self.model_id = config.active_local_model.hf_model_id
                    logger.info(f'📂 System Config requests PyTorch load for: {self.model_id}')
        except Exception as e:
            logger.info(f'Warning: Could not fetch SystemConfiguration from DB: {e}')

        # If a local model is active and we're not just a web worker, we MUST use its tokenizer
        # Otherwise the generated token IDs will decode to garbage.
        if self.model_id and self.role not in ["web", "worker"]:
            tokenizer_id = self.model_id

        logger.info(f'⚙️ Loading CPU tokenizer: {tokenizer_id}')
        self.tokenizer = AutoTokenizer.from_pretrained(tokenizer_id, token=token)
        # Load your main LLM
        if self.tokenizer.pad_token_id is None:
            self.tokenizer.pad_token_id = self.tokenizer.eos_token_id
        if self.role in ["web", "worker"]:
            logger.info(f'💻 Running in proxy mode (Role: {self.role}). Tokenizer loaded, bypassing heavy LLM load.')
            
            # Verify HTTP connection
            try:
                ping_url = f"{self.inference_url.rstrip('/')}/internal/ping/"
                res = requests.get(ping_url, timeout=3.0)
                res.raise_for_status()
                logger.info('✅ Successfully connected to inference server.')
            except requests.exceptions.RequestException as e:
                logger.info(f'⚠️ WARNING: Cannot connect to inference server at {self.inference_url}. Ensure it is running! Error: {e}')
            return

        if not self.model_id:
            logger.info('🛑 No Local AI Model selected in System Configuration. Bypassing PyTorch to save VRAM.')
            return

        logger.info('Loading Heavy AI models into VRAM...')

        compute_dtype = torch.float16
        if torch.cuda.is_available() and torch.cuda.is_bf16_supported():
            compute_dtype = torch.bfloat16
            logger.info('Hardware supports bfloat16, using it for compute.')

        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype = compute_dtype
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

        self.model.config.pad_token_id = getattr(self.tokenizer, 'pad_token_id', self.tokenizer.eos_token_id)
        logger.info(" ".join([str(x) for x in ['✅ LLM model loaded successfully.', type(self.model)]]))


        self.outline_pipeline = outline_models.Transformers(self.model, self.tokenizer)
        logger.info(" ".join([str(x) for x in ['✅ Outline llm wrapper loaded', type(self.outline_pipeline)]]))

        terminators = [self.tokenizer.eos_token_id]
        for token_str in ["<|eot_id|>", "<|im_end|>"]:
            tok_id = self.tokenizer.convert_tokens_to_ids(token_str)
            if tok_id is not None and tok_id != getattr(self.tokenizer, 'unk_token_id', None):
                terminators.append(tok_id)
                
        self.terminators = list(set(t for t in terminators if t is not None))

        logger.info('✅ AI models loaded successfully.')

    def unload_models(self):
        """Frees VRAM for model switching."""
        logger.info('🗑️ Unloading AI models...')
        del self.model
        del self.outline_pipeline
        self._generator_cache.clear()
        self.model = None
        self.outline_pipeline = None
        
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        logger.info('✅ VRAM cleared.')

    def _extract_prompts(self, messages):
        system_prompt = ""
        user_prompt = ""
        for m in messages:
            if getattr(m, 'role', '') == 'system' or getattr(m, 'type', '') == 'system':
                system_prompt += getattr(m, 'content', '') + "\n"
            elif getattr(m, 'role', '') == 'user' or getattr(m, 'type', '') == 'human':
                user_prompt += getattr(m, 'content', '') + "\n"
            elif isinstance(m, dict):
                if m.get('role') == 'system':
                    system_prompt += m.get('content', '') + "\n"
                elif m.get('role') in ['user', 'human']:
                    user_prompt += m.get('content', '') + "\n"
        return system_prompt.strip(), user_prompt.strip()

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
            
            input_tokens = self.count_conversation_tokens(messages)
            
            for text in generated_texts:
                text_to_save = text.model_dump_json(indent=2) if hasattr(text, 'model_dump_json') else str(text)
                
                output_tokens = self.count_conversation_tokens([{"role": "assistant", "content": text_to_save}])
                
                log = PromptResponseLog.objects.create(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    generated_response=text_to_save,
                    user_id=log_kwargs.get("user_id"),
                    conversation_id=log_kwargs.get("conversation_id"),
                    rag_selections=log_kwargs.get("rag_selections", ""),
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    reasoning_step_id=log_kwargs.get("reasoning_step_id")
                )
                if "log_ids" in log_kwargs:
                    log_kwargs["log_ids"].append(log.id)
        except Exception as e:
            logger.info(f'Warning: Failed to log prompt response: {e}')

    def _execute_openai_standard_request(self, messages, max_new_tokens, temperature, num_return_sequences, response_schema=None, user=None, tools=None):
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

        if tools:
            payload["tools"] = tools

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
            results = []
            for choice in data.get("choices", []):
                msg = choice.get("message", {})
                content = msg.get("content") or ""
                if "tool_calls" in msg:
                    import json
                    try:
                        tool_calls = []
                        for i, tc in enumerate(msg["tool_calls"]):
                            fn = tc.get("function", {})
                            args_str = fn.get("arguments", "{}")
                            tool_calls.append({
                                "name": fn.get("name"),
                                "args": json.loads(args_str),
                                "id": tc.get("id", f"call_{i}")
                            })
                        content = f"<tool_calls>{json.dumps(tool_calls)}</tool_calls>"
                    except Exception as e:
                        logger.error(f"Failed to parse tool_calls from OpenAI API: {e}")
                results.append(content)
            
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
            return json.dumps(response_schema.model_json_schema(), sort_keys=True)
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
            logger.info(f"⚠️ Active RAG: Already searched for '{term}'. Ignoring to prevent infinite loop.")
            return False
            
        logger.info(f"🔍 Active RAG Triggered: Halting validation to search Knowledge Graph and RAG for '{term}'...")
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
                logger.info(f'Grips search failed: {e}')
                
        # 2. Search Standard RAG
        rag_service = service_registry.rag_service
        if rag_service:
            try:
                rag_docs = rag_service.get_context(term, k=3)
                if rag_docs:
                    context_parts.append("\n\n".join([f"Source: {d.metadata.get('filename', 'Unknown')}\nContent: {d.page_content}" for d in rag_docs]))
            except Exception as e:
                logger.info(f'RAG search failed: {e}')
                
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
        """
        Attempts to validate a list of results (generated via num_return_sequences > 1) against a schema.
        
        Returns the first valid result it finds, silently discarding the invalid ones in the batch.
        This "shotgun" approach is often faster than running sequential single-generation retries.
        """
        if not raw_results:
            raise ValueError("No results were generated to validate.")
            
        last_error = None
        for res in raw_results:
            try:
                # Verify validity and return the constructed object!
                if response_schema and hasattr(response_schema, "model_validate"):
                    if isinstance(res, str):
                        return response_schema.model_validate_json(res)
                    else:
                        if isinstance(res, response_schema):
                            return res
                        elif isinstance(res, dict):
                            return response_schema.model_validate(res)
                        elif hasattr(res, "model_dump"):
                            return response_schema.model_validate(res.model_dump())
                        else:
                            return response_schema.model_validate(res)
                elif isinstance(response_schema, dict) or response_schema is None:
                    if isinstance(res, str) and response_schema is not None:
                        return json.loads(res)
                        
                # Fallback Success (unstructured strings)
                return res
            except Exception as ve:
                last_error = ve
                logger.info(f'⚠️ Validation Error against schema:\n{ve}\nRaw Output was:\n{res}')
                continue
                
        raise ValueError(f"All generated sequences failed validation. Last error: {last_error}")

    def _execute_generation_with_retries(self, generated_callable, messages, response_schema, max_new_tokens, temperature, num_return_sequences, log_kwargs, is_structured):
        """
        Universal executor handling Validation, Retries, Multiples, and Active RAG (Self-Guidance).
        Works identically for local/proxy and structured/unstructured generations.
        
        Architecture of the Execution Loops:
        1. Outer Loop (Search Hops): Allows the LLM to halt generation, issue a <SEARCH: term> command,
           and restart the generation with new context injected from the Knowledge Graph/RAG.
        2. Inner Loop (Validation Attempts): If the generated output fails to match the required 
           JSON/Pydantic schema, it retries. 
        3. Candidate Batching (num_return_sequences): On retries, it automatically increases the 
           batch size (`current_n`) to cast a wider net, increasing the probability of getting at 
           least one valid response without needing further sequential loops.
        """
        working_messages = list(messages) if isinstance(messages, list) else [{"role": "user", "content": messages}]
        max_search_hops = 4
        max_validation_attempts = 2
        searched_terms = set()
        
        last_error = None
        
        # OUTER LOOP: Active RAG Hops
        # If the LLM generates a <SEARCH:...> tag, we break the inner loop, fetch new context, 
        # append it to the messages, and start a new hop.
        for search_hop in range(max_search_hops):
            did_search = False
            
            # INNER LOOP: Validation & Generation Retries
            for attempt in range(max_validation_attempts):
                # If we fail the first attempt, increase the number of sequences generated simultaneously.
                # Generating 2+ sequences in parallel takes barely more VRAM/Time than generating 1, 
                # but drastically increases the odds that at least one passes Pydantic validation.
                current_n = num_return_sequences if attempt == 0 else max(2, num_return_sequences + 1)
                
                try:
                    raw_results = generated_callable(working_messages, max_new_tokens, temperature, current_n)
                    logger.info(f'Raw results (Hop {search_hop + 1}, Attempt {attempt + 1}): {raw_results}')
                    
                    for res in raw_results:
                        # 1. Active RAG Check: Look for <SEARCH: concept> anywhere in output
                        res_str = res if isinstance(res, str) else (res.model_dump_json() if hasattr(res, 'model_dump_json') else str(res))
                        search_match = re.search(r'<SEARCH:\s*([^>]+)>', res_str)
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
                    try:
                        valid_res = self._get_valid_structured_result(raw_results, response_schema)
                        # Success! We log working_messages so the DB reflects the Active RAG injections
                        self._log_generation(working_messages, [valid_res], log_kwargs)
                        return valid_res
                    except ValueError as ve:
                        last_error = ve
                        logger.info(f'⚠️ Validation error (Attempt {attempt + 1}/{max_validation_attempts}): {ve}')
                        continue  # Schema failed. Let the inner loop try another generation attempt.
                    
                except Exception as e:
                    last_error = e
                    logger.info(f'⚠️ Generation error (Attempt {attempt + 1}/{max_validation_attempts}): {e}')
                    continue
            
            if did_search:
                continue
            
            break  # Exhausted validation attempts without searching
                            
        # Exhausted all attempts or hops
        error_msg = f"Generation failed after {max_validation_attempts} attempts. Last error: {str(last_error)}"
        logger.info(f'❌ {error_msg}')
        if is_structured:
            err_result = {"error": "GenerationFailed", "details": error_msg}
            self._log_generation(working_messages, [err_result], log_kwargs)
            return err_result
        else:
            err_result = f"GenerationFailed: {error_msg}"
            self._log_generation(working_messages, [err_result], log_kwargs)
            return [err_result]

    def generate_response2(self, messages, max_new_tokens=1024, num_return_sequences=1, temperature=0.7, log_kwargs=None, user=None, tools=None):
        """
        Facade for standard unstructured chat completions.
        
        Delegates to `_execute_generation_with_retries` to handle proxy routing, local model 
        VRAM loading, and Active RAG loops. `num_return_sequences` can be used to generate 
        multiple alternative responses to the same prompt.
        """
        needs_proxy = self.role in ["web", "worker"]
        if user and not getattr(user, 'is_anonymous', False):
            from .models import UserActiveModel
            if UserActiveModel.objects.filter(user=user, use_external=True).exists():
                needs_proxy = True

        if needs_proxy:
            def proxy_callable(msgs, max_tok, temp, n):
                res = self._execute_openai_standard_request(msgs, max_tok, temp, n, None, user=user, tools=tools)
                return res if isinstance(res, list) else [res]
            generate_callable = proxy_callable
        else:
            def local_callable(msgs, max_tok, temp, n):
                with self._lock:
                    if self.model is None:
                        self.load_models()
                    if self.model is None:
                        raise RuntimeError("No Local AI Model is active in System Configuration, and no External API is configured for this user.")
                    msgs_summary = self.summarize_conversation(msgs)

                    prompt = self.tokenizer.apply_chat_template(
                        msgs_summary, tokenize=False, add_generation_prompt=True
                    )

                    inputs = self.tokenizer(prompt, return_tensors="pt").to(self.model.device)
                    do_sample = temp > 0.0

                    outputs = self.model.generate(
                        **inputs,
                        max_new_tokens=max_tok,
                        temperature=temp if do_sample else None,
                        top_p=0.95 if do_sample else None,
                        do_sample=do_sample,
                        eos_token_id=self.terminators,
                        pad_token_id=self.tokenizer.pad_token_id,
                        num_return_sequences=n,
                        repetition_penalty=1.05
                    )

                    prompt_length = inputs["input_ids"].shape[1]
                    generated_tokens = outputs[:, prompt_length:]
                    results = self.tokenizer.batch_decode(generated_tokens, skip_special_tokens=True)

                    return results
            generate_callable = local_callable

        return self._execute_generation_with_retries(
            generate_callable, messages, None, max_new_tokens, temperature, 
            num_return_sequences, log_kwargs, is_structured=False
        )

    def generate_outline(self, messages,
                         response_schema=None,
                         max_new_tokens=500,
                         temperature=0.7,
                         log_kwargs=None,
                         user=None,
                         num_return_sequences=1):
        """
        Facade for structured generation (JSON/Pydantic) using the `outlines` library.
        
        Args:
            messages: The conversation history or prompt.
            response_schema: A Pydantic model or JSON dict defining the required output structure.
                             The `outlines` library will mathematically constrain the LLM to this schema.
            max_new_tokens: Limit on new token generation
            temperature: scatter in generation
            log_kwargs: keyword variables of any kind to pass through to logging functions
            user: the session user resposnsible for the request
            num_return_sequences: How many parallel candidates to generate in one inference pass.
                                  Generating >1 increases the chance of passing strict Pydantic logic
                                  validation (e.g., custom @model_validators) on the very first try.
        """
        logger.info(" ".join([str(x) for x in ['Generate Outline Called', type(messages), response_schema]]))

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
            def local_callable(msgs, max_tok, temp, n):
                with self._lock:
                    if self.outline_pipeline is None:
                        self.load_models()
                        
                    if self.outline_pipeline is None:
                        raise RuntimeError("No Local AI Model is active in System Configuration.")

                    cache_key = self._get_schema_cache_key(response_schema)
                    if cache_key not in self._generator_cache:
                        actual_schema = response_schema or self.default_outline_response
                        if isinstance(actual_schema, dict):
                            schema_str = json.dumps(actual_schema)
                            try:
                                from outlines.types import JsonSchema
                                actual_schema = JsonSchema(schema_str)
                            except Exception:
                                actual_schema = schema_str
                        self._generator_cache[cache_key] = outlines.Generator(self.outline_pipeline, actual_schema)
                    
                    generator = self._generator_cache[cache_key]
                    prompt = self.tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True) if isinstance(msgs, list) else msgs
                    
                    # outlines does not take huggingface generate kwargs natively. 
                    outputs = []
                    for _ in range(n):
                        outputs.append(generator(prompt, max_new_tokens=max_tok, temperature=temp))
                    return outputs
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
                logger.info('GPU not found, falling back to CPU. This will be slow.')
                self.classifier = pipeline(
                    "zero-shot-classification",
                    model="facebook/bart-large-mnli",
                    device=-1  # Use -1 for CPU
                )


    def clean_response(self, response_content):
        assistant_response = str(response_content).strip()
        # Globally remove known special tokens that may leak into generations
        assistant_response = re.sub(r'<\|eot_id\|>|<eos>|<turn\|>|<\/s>', '', assistant_response, flags=re.IGNORECASE).strip()
        
        logger.info(" ".join([str(x) for x in ['Assistant response', assistant_response]]))
        doc = nlp(assistant_response)
        sentences = list(doc.sents)  # Convert the generator to a list

        if not sentences:
            return ""

        # Get the last detected sentence
        last_sentence = sentences[-1]

        # Check the very last token of the last sentence
        # If it's not punctuation, assume the sentence is a fragment.
            # (Removed: Stripping the last sentence blindly destroys generated python
            # code or JSON blocks that naturally don't end in punctuation).
        return assistant_response

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
            logger.info('Warning: No chat template; falling back to naive token count.')
            total_tokens = 0
            for msg in messages:
                # This misses role tokens, so it will under-count.
                total_tokens += len(self.tokenizer.encode(msg['content']))
            return total_tokens

    def summarize_conversation(self, messages: list) -> list:
        """
        Condenses conversation history to fit within the active model's context window.

        Keeps the system prompt and recent messages intact. If the total token
        count exceeds 70% of the context window, older middle messages are
        summarized by the LLM and injected as a condensed system note.

        Returns the (possibly condensed) message list — always a list.
        """
        total_tokens = self.count_conversation_tokens(messages)

        # Read context limit from the active model configuration
        try:
            from llm_api.models import SystemConfiguration
            config = SystemConfiguration.get_solo()
            if config and config.active_local_model:
                context_limit = int(config.active_local_model.context_window * 0.7)
            else:
                context_limit = 3000  # Conservative fallback
        except Exception:
            context_limit = 3000

        if total_tokens < context_limit or len(messages) <= 3:
            return messages

        # Partition: system prompt | middle (to summarize) | recent (to keep)
        system_prompt = messages[0] if messages[0].get("role") == "system" else None
        keep_recent = 4  # Keep the last 4 messages verbatim
        start_idx = 1 if system_prompt else 0

        if len(messages) - start_idx <= keep_recent:
            return messages  # Not enough to condense

        summarize_these = messages[start_idx:-keep_recent]
        recent = messages[-keep_recent:]

        summarization_instruction = [{
            "role": "system",
            "content": (
                "Condense the following conversation into a brief summary that preserves: "
                "(1) the user's core goals and requirements, "
                "(2) key decisions and conclusions reached, "
                "(3) any unresolved questions or pending tasks. "
                "Be concise — this summary replaces the original messages to save context space."
            )
        }]

        try:
            [summary_response] = self.generate_response2(
                summarization_instruction + summarize_these,
                max_new_tokens=400
            )
            summary_text = self.clean_response(summary_response)
        except Exception as e:
            logger.warning(f"Summarization failed, truncating instead: {e}")
            summary_text = f"[{len(summarize_these)} earlier messages were omitted to fit the context window.]"

        summary_msg = {
            "role": "system",
            "content": f"Summary of earlier conversation:\n{summary_text}"
        }

        condensed = []
        if system_prompt:
            condensed.append(system_prompt)
        condensed.append(summary_msg)
        condensed.extend(recent)

        return condensed