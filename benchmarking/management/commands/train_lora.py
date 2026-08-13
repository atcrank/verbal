import os
import logging
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Trains a LoRA adapter using Unsloth from a JSONL dataset (ShareGPT format)'

    def add_arguments(self, parser):
        parser.add_argument('--dataset', type=str, required=True, help='Path to the .jsonl training data')
        parser.add_argument('--output', type=str, required=True, help='Path to save the LoRA weights')
        parser.add_argument('--model', type=str, default='google/gemma-4-E2B-it', help='Base model HuggingFace ID')
        parser.add_argument('--epochs', type=int, default=3, help='Number of training epochs')
        parser.add_argument('--batch-size', type=int, default=2, help='Batch size per device')
        parser.add_argument('--rank', type=int, default=16, help='LoRA Rank (r)')

    def handle(self, *args, **options):
        dataset_path = options['dataset']
        output_path = options['output']
        model_id = options['model']
        epochs = options['epochs']
        batch_size = options['batch_size']
        rank = options['rank']

        if not os.path.exists(dataset_path):
            raise CommandError(f"Dataset not found at {dataset_path}")

        try:
            import torch
            from unsloth import FastLanguageModel
            from unsloth import get_chat_template
            from trl import SFTTrainer
            from transformers import TrainingArguments
            from datasets import load_dataset
        except ImportError as e:
            raise CommandError(f"Required ML dependencies missing. Ensure unsloth, trl, datasets are installed: {e}")

        self.stdout.write(self.style.SUCCESS(f"🚀 Starting LoRA training on {model_id}"))
        self.stdout.write(f"Dataset: {dataset_path}")
        self.stdout.write(f"Output: {output_path}")

        max_seq_length = 2048
        dtype = None
        load_in_4bit = True 

        self.stdout.write("Loading base model...")
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name = model_id,
            max_seq_length = max_seq_length,
            dtype = dtype,
            load_in_4bit = load_in_4bit,
        )

        model = FastLanguageModel.get_peft_model(
            model,
            r = rank,
            target_modules = ["q_proj", "k_proj", "v_proj", "o_proj",
                              "gate_proj", "up_proj", "down_proj"],
            lora_alpha = rank,
            lora_dropout = 0,
            bias = "none",
            use_gradient_checkpointing = "unsloth",
            random_state = 3407,
        )

        # Apply chat template (Unsloth optimized)
        tokenizer = get_chat_template(
            tokenizer,
            chat_template = "qwen-2.5",
        )

        def formatting_prompts_func(examples):
            convos = examples["conversations"]
            texts = [tokenizer.apply_chat_template(convo, tokenize=False, add_generation_prompt=False) for convo in convos]
            return { "text" : texts }

        self.stdout.write("Loading and mapping dataset...")
        dataset = load_dataset("json", data_files=dataset_path, split="train")
        dataset = dataset.map(formatting_prompts_func, batched=True)

        self.stdout.write("Initializing Trainer...")
        trainer = SFTTrainer(
            model = model,
            tokenizer = tokenizer,
            train_dataset = dataset,
            dataset_text_field = "text",
            max_seq_length = max_seq_length,
            dataset_num_proc = 2,
            packing = False,
            args = TrainingArguments(
                per_device_train_batch_size = batch_size,
                gradient_accumulation_steps = 4,
                warmup_steps = 5,
                num_train_epochs = epochs,
                learning_rate = 2e-4,
                fp16 = not torch.cuda.is_bf16_supported(),
                bf16 = torch.cuda.is_bf16_supported(),
                logging_steps = 1,
                optim = "adamw_8bit",
                weight_decay = 0.01,
                lr_scheduler_type = "linear",
                seed = 3407,
                output_dir = "outputs",
            ),
        )

        self.stdout.write("Starting training loop...")
        trainer.train()

        self.stdout.write(self.style.SUCCESS(f"Training complete! Saving adapter to {output_path}..."))
        os.makedirs(output_path, exist_ok=True)
        model.save_pretrained(output_path)
        tokenizer.save_pretrained(output_path)
        
        # Also create the DB record if we are running in full Django context
        try:
            from llm_api.models import LocalAIModel, LoRAAdapter
            base_model = LocalAIModel.objects.filter(hf_model_id=model_id).first()
            if base_model:
                adapter_name = os.path.basename(output_path)
                LoRAAdapter.objects.update_or_create(
                    name=adapter_name,
                    base_model=base_model,
                    defaults={
                        'file_path': output_path,
                        'description': f"Auto-trained for {epochs} epochs on {dataset_path}"
                    }
                )
                self.stdout.write(self.style.SUCCESS(f"Registered LoRAAdapter in database: {adapter_name}"))
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Could not register in database: {e}"))

        self.stdout.write(self.style.SUCCESS("✅ All done."))
