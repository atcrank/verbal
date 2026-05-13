from celery import shared_task
import json
import re
from llm_api.apps import service_registry
from background_resources.models import Document, RAGChunk
from .models import Reference, Citation
from background_resources.models import Document
from pydantic import BaseModel, Field
from typing import List

from bs4 import BeautifulSoup, NavigableString
from langchain.docstore.document import Document as LangChainDocument
from .api import process_pdf_with_grobid


class DocumentMetadataExtraction(BaseModel):
    title: str = Field(default="", description="The title of the document")
    authors: str = Field(default="", description="Comma separated list of authors (correct any OCR typos)")
    abstract: str = Field(default="", description="The abstract or a short summary if no formal abstract exists")
    journal: str = Field(default="", description="The journal, book, or conference name")
    publisher: str = Field(default="", description="The publisher")
    year: str = Field(default="", description="Year of publication")
    doi: str = Field(default="", description="Digital Object Identifier (DOI) if present")

class CandidateEvaluation(BaseModel):
    title: str = Field(default="", description="The correct title from the candidates, or empty")
    authors: str = Field(default="", description="The correct authors from the candidates, or empty")
    year: str = Field(default="", description="The correct year from the candidates, or empty")
    doi: str = Field(default="", description="The correct DOI from the candidates, or empty")


def _xml_to_dict(element):
    """Recursively converts a BeautifulSoup Tag to a JSON-serializable dictionary."""
    if isinstance(element, NavigableString):
        return str(element).strip()

    result = {}
    for k, v in element.attrs.items():
        result[f"@{k}"] = v

    children = [c for c in element.children if c.name is not None or (isinstance(c, NavigableString) and str(c).strip())]
    
    if not children:
        return result

    for child in children:
        if isinstance(child, NavigableString):
            text = str(child).strip()
            if text:
                result["#text"] = result.get("#text", "") + (" " if result.get("#text") else "") + text
            continue
        
        child_name = child.name
        child_dict = _xml_to_dict(child)
        
        if child_name in result:
            if not isinstance(result[child_name], list):
                result[child_name] = [result[child_name]]
            result[child_name].append(child_dict)
        else:
            result[child_name] = child_dict

    # Flatten if it only has text and no attributes
    if len(result) == 1 and "#text" in result:
        return result["#text"]

    return result


def _parse_bibl_struct(bibl) -> dict:
    """Helper to deterministically parse a <biblStruct> element into Reference fields."""
    meta = {
        "title": "", "authors": "", "journal": "",
        "publisher": "", "year": "", "doi": "", "extended_metadata": _xml_to_dict(bibl)
    }
    
    analytic = bibl.find("analytic")
    if analytic:
        title_node = analytic.find("title")
        if title_node:
             meta["title"] = title_node.text.strip()
        
        authors = []
        for author in analytic.find_all("author"):
            persName = author.find("persName")
            if persName:
                forenames = [f.text.strip() for f in persName.find_all("forename")]
                surname = persName.find("surname").text.strip() if persName.find("surname") else ""
                full_name = " ".join(forenames + [surname]).strip()
                if full_name:
                    authors.append(full_name)
        if authors:
            meta["authors"] = ", ".join(authors)

    monogr = bibl.find("monogr")
    if monogr:
        if not meta["title"]:
            title_node = monogr.find("title")
            if title_node:
                meta["title"] = title_node.text.strip()
                
        journal_node = monogr.find("title")
        if journal_node and meta["title"] != journal_node.text.strip():
            meta["journal"] = journal_node.text.strip()
            
        imprint = monogr.find("imprint")
        if imprint:
            pub_node = imprint.find("publisher")
            if pub_node:
                meta["publisher"] = pub_node.text.strip()
            date_node = imprint.find("date")
            if date_node and date_node.has_attr("when"):
                meta["year"] = date_node["when"][:4]
                
    idno = bibl.find("idno", type="DOI")
    if idno:
        meta["doi"] = idno.text.strip()
        
    return meta


def _extract_grobid_deterministic(soup: BeautifulSoup) -> dict:
    """Algorithm 1: Safely traverse the TEI XML tree for explicitly declared fields."""
    meta = {
        "title": "", "authors": "", "abstract": "", "journal": "",
        "publisher": "", "year": "", "doi": "", "extended_metadata": {}
    }
    
    tei_header = soup.find("teiHeader")
    if not tei_header:
        return meta

    title_node = tei_header.find("titleStmt")
    if title_node and title_node.find("title"):
        meta["title"] = title_node.find("title").text.strip()

    abstract_node = tei_header.find("abstract")
    if abstract_node:
        meta["abstract"] = abstract_node.text.strip()
        
    source_desc = tei_header.find("sourceDesc")
    if source_desc:
        bibl = source_desc.find("biblStruct")
        if bibl:
            bibl_meta = _parse_bibl_struct(bibl)
            if not meta["title"] and bibl_meta["title"]:
                meta["title"] = bibl_meta["title"]
            meta["authors"] = bibl_meta["authors"]
            meta["journal"] = bibl_meta["journal"]
            meta["publisher"] = bibl_meta["publisher"]
            meta["year"] = bibl_meta["year"]
            meta["doi"] = bibl_meta["doi"]
            meta["extended_metadata"] = bibl_meta["extended_metadata"]
                
    return meta


def _extract_grobid_heuristics(soup: BeautifulSoup) -> dict:
    """Algorithm 2a: Scrape loose tags that Grobid couldn't confidently classify."""
    candidates = {"title": [], "authors": [], "year": [], "doi": []}
    
    front = soup.find("front")
    if front:
        for tag in front.find_all(["head", "note"]):
            text = tag.text.strip()
            if 5 < len(text) < 150 and text not in candidates["title"]:
                candidates["title"].append(text)
                
        for affil in front.find_all("affiliation"):
            text = affil.text.strip()
            if text and text not in candidates["authors"]:
                candidates["authors"].append(text)
            
        for date_tag in front.find_all("date"):
            text = date_tag.text.strip()
            if text and text not in candidates["year"]:
                candidates["year"].append(text)
                
    # Basic DOI regex search in first 2000 chars as a heuristic fallback
    front_text = soup.get_text(separator=" ", strip=True)[:2000]
    doi_matches = re.findall(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", front_text, re.IGNORECASE)
    for match in doi_matches:
        if match not in candidates["doi"]:
            candidates["doi"].append(match)
            
    return candidates


def _evaluate_candidates_with_llm(candidates: dict, ai_service) -> dict:
    """Algorithm 2b: Ask the LLM to verify and route the heuristic candidates."""
    if not any(candidates.values()):
        return {}
        
    prompt = f"""
    Evaluate the following candidate text snippets extracted from a document.
    Identify if any of them represent the document's Title, Authors, Year of Publication, or DOI.
    Return ONLY the correct value for each field if found among the candidates, otherwise return an empty string.
    
    Candidates:
    {json.dumps(candidates, indent=2)}
    """
    
    result = ai_service.generate_outline(
        messages=[{"role": "user", "content": prompt}],
        response_schema=CandidateEvaluation,
        max_new_tokens=500
    )
    
    eval_obj = None
    if isinstance(result, dict) and "error" not in result:
        eval_obj = CandidateEvaluation.model_validate(result)
    elif isinstance(result, str):
        try:
            eval_obj = CandidateEvaluation.model_validate_json(result)
        except Exception:
            pass
    elif hasattr(result, 'title'):
        eval_obj = result
        
    if eval_obj:
        return {
            "title": eval_obj.title, "authors": eval_obj.authors,
            "year": eval_obj.year, "doi": eval_obj.doi
        }
    return {}


def _extract_fallback_with_llm(front_text: str, missing_fields: list, ai_service) -> dict:
    """Algorithm 3: Brute force OCR reading for remaining missing fields."""
    if not missing_fields or not front_text:
        return {}
        
    prompt = f"""
    You are an expert academic librarian. Analyze the OCR text from the beginning of this document.
    Extract the following missing metadata fields: {", ".join(missing_fields)}.
    If there are obvious OCR errors (e.g. "Trru UNIvERSITY op MEvPrrts"), seamlessly correct them to their obvious real-world counterparts.
    
    OCR TEXT:
    {front_text}
    """
    
    result = ai_service.generate_outline(
        messages=[{"role": "user", "content": prompt}],
        response_schema=DocumentMetadataExtraction,
        max_new_tokens=1000
    )
    
    meta_obj = None
    if isinstance(result, dict) and "error" not in result:
        meta_obj = DocumentMetadataExtraction.model_validate(result)
    elif isinstance(result, str):
        try:
            meta_obj = DocumentMetadataExtraction.model_validate_json(result)
        except Exception:
            pass
    elif hasattr(result, 'title'):
        meta_obj = result
        
    if meta_obj:
        return {
            "title": meta_obj.title, "authors": meta_obj.authors,
            "abstract": meta_obj.abstract, "journal": meta_obj.journal,
            "publisher": meta_obj.publisher, "year": meta_obj.year,
            "doi": meta_obj.doi
        }
    return {}

@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={'max_retries': 2})
def task_extract_grobid_metadata(self, document_id: int):
    """
    Sends a PDF to Grobid, parses the TEI XML, and populates the Citation Graph.
    """
    try:
        doc = Document.objects.get(id=document_id)
    except Document.DoesNotExist:
        return f"Document {document_id} not found."

    if not doc.file.name.lower().endswith('.pdf'):
        return f"Document {doc.title} is not a PDF. Grobid processing skipped."

    file_path = doc.file.path
    tei_xml = process_pdf_with_grobid(file_path)
    
    soup = BeautifulSoup(tei_xml, "xml")
    ai_service = service_registry.ai_service
    
    # Step 1: Deterministic extraction from Grobid TEI XML
    meta = _extract_grobid_deterministic(soup)

    # Step 2: Heuristic extraction & LLM Evaluation
    missing_fields = [k for k, v in meta.items() if not v]
    if missing_fields:
        candidates = _extract_grobid_heuristics(soup)
        if any(candidates.values()):
            eval_results = _evaluate_candidates_with_llm(candidates, ai_service)
            for k, v in eval_results.items():
                if v and not meta.get(k):
                    meta[k] = v
                    
    # Step 3: LLM Full Document Fallback
    missing_fields = [k for k, v in meta.items() if not v]
    if missing_fields:
        tei_header = soup.find("teiHeader")
        front_text = tei_header.get_text(separator="\n", strip=True) if tei_header else ""
        if len(front_text) < 200:
            front_text = soup.get_text(separator="\n", strip=True)[:4000]
        else:
            front_text = front_text[:4000]
            
        fallback_results = _extract_fallback_with_llm(front_text, missing_fields, ai_service)
        for k, v in fallback_results.items():
            if v and not meta.get(k):
                meta[k] = v
    
    # Create or update the Reference for the source document
    source_ref, _ = Reference.objects.update_or_create(
        document=doc,
        defaults={
            "title": meta.get("title") or doc.title,
            "authors": meta.get("authors") or "",
            "abstract": meta.get("abstract") or "",
            "journal": meta.get("journal") or "",
            "publisher": meta.get("publisher") or "",
            "year": meta.get("year") or "",
            "doi": meta.get("doi") or "",
            "extended_metadata": meta.get("extended_metadata") or {},
            "tei_xml": tei_xml
        }
    )
    
    # 2. Extract Bibliography (Citations)
    list_bibl = soup.find("listBibl")
    
    # Pre-compute contexts by finding all in-text <ref type="bibr"> tags
    # and mapping them to their parent paragraphs.
    context_map = {}
    for ref in soup.find_all("ref", type="bibr"):
        target = ref.get("target")
        if target and target.startswith("#"):
            bib_id = target[1:]
            parent_p = ref.find_parent("p")
            if parent_p:
                if bib_id not in context_map:
                    context_map[bib_id] = []
                p_text = parent_p.text.strip()
                # Avoid adding identical paragraphs if multiple citations occur in the same block
                if p_text not in context_map[bib_id]:
                    context_map[bib_id].append(p_text)
                    
    if list_bibl:
        for bibl in list_bibl.find_all("biblStruct"):
            bib_id = bibl.get("xml:id")
            bib_meta = _parse_bibl_struct(bibl)
            cited_title = bib_meta["title"]
            raw_string = bibl.text.strip()
            
            contexts = context_map.get(bib_id, [])
            context_text = "\n\n".join(contexts) if contexts else ""
            
            if cited_title:
                # Create a "Ghost" reference for the cited work
                target_ref, _ = Reference.objects.get_or_create(
                    title=cited_title,
                    defaults={
                        "document": None, # We don't have the file for it yet!
                        "authors": bib_meta["authors"],
                        "journal": bib_meta["journal"],
                        "publisher": bib_meta["publisher"],
                        "year": bib_meta["year"],
                        "doi": bib_meta["doi"],
                        "extended_metadata": bib_meta.get("extended_metadata") or {}
                    } 
                )
                
                # Link them
                Citation.objects.get_or_create(
                    source_reference=source_ref,
                    target_reference=target_ref,
                    defaults={
                        "raw_reference_string": raw_string,
                        "context_text": context_text
                    }
                )
                
    return f"Grobid extraction complete for {doc.title}. Extracted citations."


def grobid_tei_to_semantic_chunks(tei_xml_string, document_title=""):
    soup = BeautifulSoup(tei_xml_string, "xml")
    semantic_chunks = []

    # In TEI XML, sections are usually wrapped in <div> tags
    for div in soup.find_all("div"):
        # Extract the section header
        head = div.find("head")
        section_title = head.text.strip() if head else "Un-headered Section"

        # Extract all paragraphs within this specific section
        paragraphs = [p.text.strip() for p in div.find_all("p") if p.text]
        section_text = "\n\n".join(paragraphs)

        if len(section_text) > 50:
            # Prefix the section title with the document title to prevent collision
            # e.g., "Attention is All You Need - Methodology"
            compound_title = f"{document_title} - {section_title}" if document_title else section_title
            
            # We now have a chunk that represents an exact, author-defined section!
            doc = LangChainDocument(
                page_content=section_text,
                metadata={
                    "section_title": compound_title,
                    "is_semantic_chunk": True
                }
            )
            semantic_chunks.append(doc)

    return semantic_chunks