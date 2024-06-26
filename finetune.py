import yaml
import torch
from datasets import load_dataset
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    TrainingArguments,
    AutoConfig
)
from peft import LoraConfig, PeftModel
from trl import SFTTrainer
import os

def load_config(config_path):
    '''Load configurations from YAML file.'''
    with open(config_path, 'r') as file:
        return yaml.safe_load(file)

# Load configurations from YAML file
config = load_config('config.yaml')

# Setup configurations
access_token = config['access_token']
model_cfg = config['model_config']
q_lora_params = config['q_lora_parameters']
bitsandbytes_params = config['bitsandbytes_parameters']
train_args_cfg = config['training_arguments']
sft_params = config['sft_parameters']

# Initialize configuration with specified path
model_path = model_cfg['model_path']
new_model_path = model_cfg['new_model_path']

# Prepare model, tokenizer, and datasets
tokenizer = AutoTokenizer.from_pretrained(model_path, use_fast=True, token=access_token['huggingface_token'])
dataset = load_dataset(model_cfg['dataset_path'], model_cfg['dataset_config'], split="train")

# Set BitsAndBytes Quantization configuration
compute_dtype = getattr(torch, bitsandbytes_params['bnb_4bit_compute_dtype'])
bnb_config = BitsAndBytesConfig(
    load_in_4bit=bitsandbytes_params['use_4bit'],
    bnb_4bit_quant_type=bitsandbytes_params['bnb_4bit_quant_type'],
    bnb_4bit_compute_dtype=compute_dtype,
    bnb_4bit_use_double_quant=bitsandbytes_params['use_nested_quant'],
)

# Check GPU compatibility with bfloat16
if compute_dtype == torch.float16 and bitsandbytes_params['use_4bit']:
    major, _ = torch.cuda.get_device_capability()
    if major >= 8:
        print("=" * 80)
        print("Your GPU supports bfloat16: accelerate training with bf16=True")
        print("=" * 80)

# Load base model
model = AutoModelForCausalLM.from_pretrained(
    model_path,
    quantization_config=bnb_config,
    device_map=sft_params['device_map'],
    token=access_token['huggingface_token']
)

model.config.use_cache = False
model.config.pretraining_tp = 1

# Load LLaMA tokenizer
tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
tokenizer.pad_token = tokenizer.eos_token
tokenizer.padding_side = "right" # Fix weird overflow issue with fp16 training

# Load LoRA configuration
peft_config = LoraConfig(
    lora_alpha=q_lora_params['lora_alpha'],
    lora_dropout=q_lora_params['lora_dropout'],
    r=q_lora_params['lora_r'],
    bias="none",
    task_type="CAUSAL_LM",
)


# Set TrainingArguments
training_arguments = TrainingArguments(
    output_dir=train_args_cfg['output_dir'],
    num_train_epochs=train_args_cfg['num_train_epochs'],
    per_device_train_batch_size=train_args_cfg['per_device_train_batch_size'],
    gradient_accumulation_steps=train_args_cfg['gradient_accumulation_steps'],
    optim=train_args_cfg['optim'],
    save_steps=train_args_cfg['save_steps'],
    logging_steps=train_args_cfg['logging_steps'],
    learning_rate=train_args_cfg['learning_rate'],
    weight_decay=train_args_cfg['weight_decay'],
    fp16=train_args_cfg['fp16'],
    bf16=train_args_cfg['bf16'],
    max_grad_norm=train_args_cfg['max_grad_norm'],
    max_steps=train_args_cfg['max_steps'],
    warmup_ratio=train_args_cfg['warmup_ratio'],
    group_by_length=train_args_cfg['group_by_length'],
    lr_scheduler_type=train_args_cfg['lr_scheduler_type'],

)

# Initialize trainer
trainer = SFTTrainer(
    model=model,
    train_dataset=dataset,
    peft_config=peft_config,
    # dataset_text_field="text",
    max_seq_length=sft_params['max_seq_length'],
    tokenizer=tokenizer,
    args=training_arguments,
    packing=sft_params['packing'],
)

# Start training
trainer.train()

# Save the lora model
new_model_path_lora = new_model_path + "-lora"
trainer.model.save_pretrained(new_model_path_lora)
print("Model training complete and peft adapter saved to:", new_model_path_lora)

# merge lora model with base model
torch.cuda.empty_cache()
model = AutoModelForCausalLM.from_pretrained(model_path)
model = PeftModel.from_pretrained(model, new_model_path_lora)
model = model.merge_and_unload()
tokenizer.pad_token = tokenizer.eos_token
tokenizer.padding_side = "right"

# merge 
model.save_pretrained(new_model_path)
tokenizer.save_pretrained(new_model_path)
print("Model load complete and full model saved to:", new_model_path)
