import os
import gc
from django.conf import settings
from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline, BitsAndBytesConfig
import outlines

import torch
import spacy
from pydantic import BaseModel
import typing
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

    model_id = None
    model = None
    classifier = None
    llm_pipeline = None
    outline_pipeline = None
    terminators = None
    embedding_model = None
    tokenizer = None
    default_outline_response = DefaultOutlineResponse


    def load_models(self):
        # This method is called only once at startup.
        print("🚀 Loading AI models into memory...")
        token = os.getenv("HF_TOKEN")
        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.float16
        )
        
        # 1. Determine Model ID
        # Try Database first
        self.model_id = None
        try:
            from .models import AIModel
            active_model = AIModel.objects.filter(is_active=True).first()
            if active_model:
                print(f"📂 Found active model in DB: {active_model.name} ({active_model.hf_model_id})")
                self.model_id = active_model.hf_model_id
                # We could also use active_model.load_in_4bit here to config quantization
        except Exception as e:
            print(f"Warning: Could not fetch AIModel from DB (migrations might be pending): {e}")

        # Fallback to Settings
        if not self.model_id:
            self.model_id = getattr(settings, "LLM_MODEL_ID", os.getenv("LLM_MODEL_ID", "microsoft/Phi-3-mini-4k-instruct"))
            print(f"⚙️ Using default/settings model: {self.model_id}")

        self.tokenizer = AutoTokenizer.from_pretrained(self.model_id, token=token)
        # Load your main LLM
        if self.tokenizer.pad_token_id is None:
            self.tokenizer.pad_token_id = self.tokenizer.eos_token_id
        # Ensure the model knows this too
        self.model = AutoModelForCausalLM.from_pretrained(
            self.model_id,
            # dtype=torch.float16,
            device_map={"": 0},
            quantization_config=quantization_config,
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
        self.model = None
        self.llm_pipeline = None
        self.outline_pipeline = None
        
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        print("✅ VRAM cleared.")

    def generate_response(self, messages, max_new_tokens, num_return_sequences=1, temperature=0.7   ):
        messages = self.summarize_conversation(messages)
        response = self.llm_pipeline(messages,
                                     do_sample=True,
                                     top_p=0.95,
                                     temperature=temperature,
                                     max_new_tokens=max_new_tokens,
                                     eos_token_id=self.terminators,
                                     num_return_sequences=num_return_sequences,
                                     return_full_text=False)
        print(response)
        if num_return_sequences > 1:
            return [r['generated_text'] for r in response]
        return [response[0]['generated_text']]


    def generate_outline(self, messages,
                         response_schema=None,
                         max_new_tokens=500,
                         temperature=0.7,
                         num_return_sequences=1):
        # lazy_load outline pipeline
        print("Generate Outline Called", type(messages), response_schema)
        if response_schema is None:
            response_schema = self.default_outline_response
        
        if isinstance(messages, list):
            prompt = self.tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True
            )
        else:
            prompt = messages

        # https://dottxt-ai.github.io/outlines/latest/features/core/generator/
        generator = outlines.Generator(self.outline_pipeline, response_schema)

        return generator(prompt,
                         do_sample=True,
                         max_new_tokens=max_new_tokens,
                         temperature=temperature,
                         # num_beams=num_return_sequences,
                         num_return_sequences=num_return_sequences)

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
        assistant_response = response_content
        print("Assistant response", assistant_response)
        doc = nlp(assistant_response)
        sentences = list(doc.sents)  # Convert the generator to a list

        if not sentences:
            return []

        # Get the last detected sentence
        last_sentence = sentences[-1]

        # Check the very last token of the last sentence
        # If it's not punctuation, assume the sentence is a fragment.
        if not last_sentence[-1].is_punct:
            # Return all sentences except the last one
            return "".join([sent.text.strip() for sent in sentences[:-1]])
        else:
            # All detected sentences seem complete
            return "".join([sent.text.strip() for sent in sentences])

    def count_conversation_tokens(self, messages: list) ->int:
        if not self.tokenizer:
            raise ValueError("Tokenizer not loaded. Call load_models() first.")

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