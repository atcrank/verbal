import re
import spacy
from spacy.language import Language
from scispacy.abbreviation import AbbreviationDetector
import contextualSpellCheck
from spellchecker import SpellChecker

class NLPService:

    primary_nlp = None
    abbreviation_model = None
    contextual_spellcheck_model = None
    basic_spellcheck_model = None
    domain_terms = ["term1", "term2", "term3"]

    def get_primary_nlp(self):
        if self.primary_nlp is None:
            print("Loading Spacy Model (this should happen once)...")
            # Load the base model
            self.primary_nlp = spacy.load("en_core_web_sm")
        return self.primary_nlp


    def get_abbreviation_model(self):
        """Lazy-loads the heavy Spacy model."""

        if self.abbreviation_model is None:
            print("Loading Spacy Model (this should happen once)...")
            # Load the base model
            self.abbreviation_model = spacy.load("en_core_web_sm")

            # Add the Abbreviation Detector
            # (We add it to the pipe once during load)
            if "abbreviation_detector" not in self.abbreviation_model.pipe_names:
                self.abbreviation_model.add_pipe("abbreviation_detector")
        return self.abbreviation_model

    def get_contextual_spellcheck_model(self):
        if self.contextual_spellcheck_model is None:
            print("Loading Spacy Model (this should happen once)...")
            # Load the base model
            self.contextual_spellcheck_model = spacy.load("en_core_web_sm")

            # Add the Abbreviation Detector
            # (We add it to the pipe once during load)
            if "contextual spellchecker" not in self.contextual_spellcheck_model.pipe_names:
               contextualSpellCheck.add_to_pipe(self.contextual_spellcheck_model)
        return self.contextual_spellcheck_model

    def get_basic_spellchecker(self):
        if self.basic_spellcheck_model is None:
            self.basic_spellcheck_model = SpellChecker()
            # self.basic_spellcheck_model.word_frequency.load_words() # Optional
        return self.basic_spellcheck_model

    # 2. Functional Interfaces (The "Public API")
    # Your application code calls THESE, never touching 'nlp' directly.

    def extract_acronyms(self, text: str) -> dict[str, str]:
        """
        Returns a dictionary of {Acronym: Definition}.
        """
        if self.abbreviation_model is None:
            self.get_abbreviation_model()
            
        doc = self.abbreviation_model(text)

        # Return unique mappings
        return {
            abrv.text: abrv._.long_form.text
            for abrv in doc._.abbreviations
        }


    def contextual_spellcheck(self, text: str) -> str:
        """
        Contextual spell-checker uses a Bert model and may substitute a more likely word rather than the closest word with corrected spelling.
        The advantage is that the wrong word is likely to be detected (e.g. steal as a misspelling of steel)
        """
        if self.contextual_spellcheck_model is None:
            self.get_contextual_spellcheck_model()
            
        doc = self.contextual_spellcheck_model(text)
        return doc._.outcome_spell_checked



    def basic_spellcheck(self, text: str) -> str:

        """
        Takes a raw string, identifies typos, and returns the corrected string.
        Note: This is a simple implementation. For complex sentence structures,
        be careful not to over-correct.
        """
        if self.basic_spellcheck_model is None:
            self.get_basic_spellchecker()

        # Simple tokenization that preserves punctuation usage might be needed,
        # but for spell checking, we often just want to fix words.
        # A simple regex to find words:
        tokens = re.findall(r'\b\w+\b', text)

        misspelled = self.basic_spellcheck_model.unknown(tokens)

        corrected_text = text
        for word in misspelled:
            correction = self.basic_spellcheck_model.correction(word)
            if correction and correction != word:
                # Replace using regex to match whole words only (avoid replacing 'th' in 'the')
                corrected_text = re.sub(r'\b' + re.escape(word) + r'\b', correction, corrected_text)

        return corrected_text



    def get_lemmatized_tokens(self, text: str) -> list[str]:
        """
        Useful for search normalization later.
        """
        if self.primary_nlp is None:
            self.get_primary_nlp()
            
        doc = self.primary_nlp(text)
        return [token.lemma_ for token in doc if not token.is_stop]