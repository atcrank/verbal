import uuid
from pydantic import BaseModel, Optional
import outlines
from langchain.retrievers.multi_vector import MultiVectorRetriever
from langchain.storage import InMemoryByteStore
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from llm_api.api import service_registry

# 1. The Distillation Chain (Run ONCE during ingestion)
# This distills a complex chunk into a dense "proposition" or summary
ai_service = service_registry.get('ai_service')
rag_service = service_registry.get('rag_service')

vectorstore = rag_service.vectorstore

# remove tables of contents etc.
import re


def is_likely_toc(text_chunk: str) -> bool:
    lines = text_chunk.split('\n')
    if not lines:
        return False

    # 1. Check for "dots" pattern (e.g., "Chapter 1 .......... 5")
    dot_pattern = re.compile(r'\.{3,}\s*\d+$')

    # 2. Check for lines ending in numbers (heuristic for page nums)
    #    We check if > 30% of lines end with a number
    lines_ending_in_number = sum(1 for line in lines if re.search(r'\s\d+$', line.strip()))

    # 3. Keyword check (optional, can be risky if strictly applied)
    has_toc_header = any("contents" in line.lower() for line in lines[:3])

    # Decision logic
    if has_toc_header and lines_ending_in_number > len(lines) * 0.2:
        return True

    # Strong signal: dots + numbers
    dot_matches = sum(1 for line in lines if dot_pattern.search(line))
    if dot_matches > 3:
        return True

    return False


# Usage in your loading loop
clean_docs = [doc for doc in all_docs if not is_likely_toc(doc.page_content)]

#  === Distillation Chain ===
# Define the ingestion schema
class DocumentIngestion(BaseModel):
    is_structural_noise: bool
    summary: Optional[str]

# Setup your local model (e.g., Mistral or Llama 3)

generator = outlines.generate.json(ai_service.outline_pipeline, DocumentIngestion)

def process_chunk(chunk_text):
    prompt = f"""
    You are a data ingestion agent. 
    Analyze the following document chunk. 
    1. If it is a Table of Contents, Index, or Copyright page, set is_structural_noise to True.
    2. Otherwise, write a dense, fact-heavy summary for retrieval.

    Chunk:
    {chunk_text[:2000]} # Truncate for speed if needed
    """

    result = generator(prompt)

    if result.is_structural_noise:
        return "SKIP"
    return result.summary

# 2. Setup Stores
store = InMemoryByteStore() # Stores the heavy raw chunks
id_key = "doc_id"

# 3. The Retriever
retriever = MultiVectorRetriever(
    vectorstore=vectorstore,
    byte_store=store,
    id_key=id_key,
)

# 4. Ingestion Loop
doc_ids = [str(uuid.uuid4()) for _ in docs]
summaries = ai_service.outline_pipeline.batch(docs, {"max_concurrency": 1})

# Filter out the "SKIP" summaries (your TOCs)
filtered_summaries = []
filtered_docs = []
filtered_ids = []

for i, summary in enumerate(summaries):
    if "SKIP" not in summary:
        # Create a document specifically for the VECTOR store (the summary)
        summary_doc = Document(page_content=summary, metadata={id_key: doc_ids[i]})
        filtered_summaries.append(summary_doc)
        filtered_docs.append(docs[i])
        filtered_ids.append(doc_ids[i])

# Add to stores
retriever.vectorstore.add_documents(filtered_summaries)
retriever.docstore.mset(list(zip(filtered_ids, filtered_docs)))