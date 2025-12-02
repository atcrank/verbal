
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, pipeline


# --- Part 1: Load your local LLM (same as before) ---
model_id = "meta-llama/Meta-Llama-3-8B-Instruct"
tokenizer = AutoTokenizer.from_pretrained(model_id)
model = AutoModelForCausalLM.from_pretrained(
    model_id,
    torch_dtype=torch.bfloat16,
    device_map="auto",
    load_in_4bit=True,
)
llm_pipeline = pipeline(
    "text-generation",
    model=model,
    tokenizer=tokenizer,
    max_new_tokens=512,
    eos_token_id=tokenizer.eos_token_id,
)

# --- Part 2: Build the RAG Pipeline with LangChain ---
from langchain.text_splitter import RecursiveCharacterTextSplitter, CharacterTextSplitter
from langchain.embeddings import HuggingFaceEmbeddings
from langchain.vectorstores import FAISS

# This is our local knowledge base. In a real app, you would load this from files.
knowledge_base_texts = [
    "The Client Record System, often abbreviated as CRS, is the primary database for all customer interactions. It was deployed in 2021.",
    "Project Phoenix is the codename for the complete overhaul of the CRS. The goal is to migrate from the legacy monolith to a microservices architecture by Q4 2025.",
    "A Product Disclosure Statement (PDS) is a legal document that must be provided to clients before they purchase a financial product. The PDS outlines the product's features, risks, and fees.",
    "The Quality Management System (QMS) is a set of business processes designed to ensure products consistently meet customer requirements. The QMS must be updated during Project Phoenix."
]

with open("knowledge_sources/FirefightingGlossary.txt", "r") as f:
    firefighting_definitions = f.read()

print("📚 Indexing knowledge base...")

# 1. Chunk the documents
text_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
glossary_splitter = CharacterTextSplitter(chunk_size=1000, separator="\n", chunk_overlap=0)
definitions = glossary_splitter.create_documents(firefighting_definitions)

# 2. Create local embeddings
# This model runs entirely on your machine.
embedding_model_name = "sentence-transformers/all-MiniLM-L6-v2"
embedding_model = HuggingFaceEmbeddings(model_name=embedding_model_name)

# 3. Create the Vector Store (FAISS)
# This step creates the vectors and stores them in memory.

print("✅ Indexing complete.")
db = FAISS.from_documents(definitions, embedding_model)

# You can now save it locally to be loaded later
db.save_local("faiss_firefighting_glossary")

print(f"Successfully created a FAISS index with {len(definitions)} entries.")
print("Each entry corresponds to one line from your glossary file.")

# --- Part 3: Run a query using the RAG pipeline ---

user_query = "What considerations should be made for the QMS during the CRS migration under Project Phoenix?"

print(f"\n🤔 User Query: {user_query}")

# 4. Retrieve relevant context
# The vector store finds the most relevant chunks from our knowledge base.
retrieved_docs = db.similarity_search(user_query, k=2) # Get top 2 results
retrieved_context = "\n\n".join([doc.page_content for doc in retrieved_docs])

print(f"\n🧠 Retrieved Context:\n{retrieved_context}")

# 5. Augment the prompt and generate
# We create a prompt template that instructs the LLM how to use the provided context.
prompt_template = """
<|begin_of_text|>
<|start_header_id|>system<|end_header_id|>
You are an expert assistant. Use the following context to answer the user's question. If you don't know the answer from the context, say that you don't know. Do not make up information.

CONTEXT:
{context}
<|eot_id|>

<|start_header_id|>user<|end_header_id|>
QUESTION:
{question}
<|eot_id|>

<|start_header_id|>assistant<|end_header_id|>
"""

augmented_prompt = prompt_template.format(context=retrieved_context, question=user_query)

print("\n🤖 Generating answer with retrieved context...")

# 6. Get the final response from the local LLM
response = llm_pipeline(augmented_prompt)
print("\n✅ Final Answer:\n")
print(response[0]['generated_text'].split("<|start_header_id|>assistant<|end_header_id|>")[1].strip())