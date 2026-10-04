import os
from unittest.mock import patch, MagicMock
from bs4 import BeautifulSoup
from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from background_resources.models import Document
from .models import Reference, Citation
from .tasks import (
    _extract_grobid_deterministic,
    _parse_bibl_struct,
    _xml_to_dict,
    task_extract_grobid_metadata
)


class GrobidModelTests(TestCase):
    """Unit tests for Reference and Citation models and string truncation safeguards."""

    def test_reference_clean_and_save_truncation(self):
        """Long strings exceeding column limits must be cleanly truncated to avoid DB DataErrors."""
        doc = Document.objects.create(
            title="Short Title",
            file=SimpleUploadedFile("dummy.pdf", b"%PDF-1.4 dummy content")
        )
        long_string = "A" * 2000
        ref = Reference(
            document=doc,
            title=long_string,
            journal=long_string,
            publisher=long_string,
            year=long_string,
            publication_date=long_string,
            volume=long_string,
            issue=long_string,
            pages=long_string,
            doi=long_string
        )
        ref.clean()
        self.assertEqual(len(ref.title), 999)
        self.assertTrue(ref.title.endswith("..."))
        self.assertEqual(len(ref.journal), 999)
        self.assertEqual(len(ref.publisher), 254)
        self.assertEqual(len(ref.year), 199)
        self.assertEqual(len(ref.publication_date), 49)
        self.assertEqual(len(ref.volume), 49)
        self.assertEqual(len(ref.issue), 49)
        self.assertEqual(len(ref.pages), 49)
        self.assertEqual(len(ref.doi), 99)

        # Ensure save() persists without database error
        ref.save()
        self.assertIsNotNone(ref.id)

    def test_reference_and_citation_str_representation(self):
        """String representations should format human-readable summaries."""
        doc = Document.objects.create(
            title="Firefighting Robotics 2023",
            file=SimpleUploadedFile("test.pdf", b"%PDF-1.4 content")
        )
        ref1 = Reference.objects.create(document=doc, title="Firefighting Robotics 2023")
        ref2 = Reference.objects.create(title="Thermal Sensors Survey")

        self.assertEqual(str(ref1), "Firefighting Robotics 2023")

        citation = Citation.objects.create(
            source_reference=ref1,
            target_reference=ref2,
            raw_reference_string="Talavera (2021). Thermal Sensors Survey."
        )
        self.assertIn("Firefighting Robotics 2023", str(citation))
        self.assertIn("Thermal Sensors Survey", str(citation))

        # Unlinked citation
        unlinked = Citation.objects.create(
            source_reference=ref1,
            target_reference=None,
            raw_reference_string="Unlinked reference string."
        )
        self.assertIn("Unlinked Reference", str(unlinked))


class GrobidDeterministicParsingTests(TestCase):
    """Unit tests for deterministic TEI XML traversal algorithms."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        fixture_path = os.path.join(os.path.dirname(__file__), 'test_data', 'sample_tei.xml')
        with open(fixture_path, 'r', encoding='utf-8') as f:
            cls.tei_xml = f.read()
        cls.soup = BeautifulSoup(cls.tei_xml, "xml")

    def test_extract_grobid_deterministic(self):
        """Verify extraction of title, authors, journal, publisher, year, DOI, and abstract."""
        meta = _extract_grobid_deterministic(self.soup)

        self.assertEqual(meta["title"], "Autonomous Robotics for Indoor Firefighting Search and Rescue")
        self.assertIn("Juan Talavera", meta["authors"])
        self.assertIn("Jacques Penders", meta["authors"])
        self.assertEqual(meta["journal"], "Journal of Field Robotics")
        self.assertEqual(meta["publisher"], "Wiley")
        self.assertEqual(meta["year"], "2023")
        self.assertEqual(meta["doi"], "10.1002/rob.22150")
        self.assertIn("autonomous ground robot architecture", meta["abstract"])
        self.assertIn("zero-visibility smoke environments", meta["abstract"])

    def test_parse_bibl_struct(self):
        """Verify parsing of individual bibliography items into reference dictionaries."""
        bibl_nodes = self.soup.find_all("biblStruct")
        # There should be at least the main header biblStruct and 2 in listBibl
        self.assertTrue(len(bibl_nodes) >= 3)

        # Test the first reference in listBibl (b0)
        list_bibl = self.soup.find("listBibl")
        self.assertIsNotNone(list_bibl)
        ref_b0 = list_bibl.find("biblStruct", attrs={"xml:id": "b0"})
        self.assertIsNotNone(ref_b0)

        meta_b0 = _parse_bibl_struct(ref_b0)
        self.assertEqual(meta_b0["title"], "Swarm Intelligence for Disaster Relief")
        self.assertIn("Jacques Penders", meta_b0["authors"])
        self.assertEqual(meta_b0["doi"], "10.1016/j.adhoc.2018.02.001")
        self.assertEqual(meta_b0["year"], "2018")
        self.assertEqual(meta_b0["publisher"], "Elsevier")

    def test_xml_to_dict_conversion(self):
        """Verify recursive conversion of BeautifulSoup elements to dictionary."""
        analytic_node = self.soup.find("analytic")
        self.assertIsNotNone(analytic_node)
        d = _xml_to_dict(analytic_node)
        self.assertIsInstance(d, dict)
        self.assertIn("title", d)
        self.assertIn("author", d)


class GrobidTaskErrorHandlingTests(TestCase):
    """Unit tests for task error handling, offline resilience, and non-PDF rejection."""

    def test_task_nonexistent_document_handled(self):
        """Calling task on non-existent document ID returns descriptive string without crash."""
        res = task_extract_grobid_metadata.call(999999)
        self.assertIn("Document 999999 not found", res)

    def test_task_non_pdf_document_skipped(self):
        """Text or non-PDF files are gracefully rejected by the Grobid extractor."""
        doc = Document.objects.create(
            title="Firefighting Glossary",
            file=SimpleUploadedFile("glossary.txt", b"Thermal Imaging: ...\nIncident Command: ...")
        )
        res = task_extract_grobid_metadata.call(doc.id)
        self.assertIn("is not a PDF. Grobid processing skipped", res)

    @patch("grobid_client.tasks.process_pdf_with_grobid")
    def test_task_grobid_connection_error_gracefully_handled(self, mock_grobid):
        """Connection errors to the GROBID daemon return clean status without crashing."""
        mock_grobid.side_effect = ConnectionError("Connection to Grobid at localhost:8070 refused")
        doc = Document.objects.create(
            title="Fire Paper",
            file=SimpleUploadedFile("paper.pdf", b"%PDF-1.4 dummy content")
        )
        res = task_extract_grobid_metadata.call(doc.id)
        self.assertIn("Grobid service unavailable", res)
        self.assertIn("refused", res)

    @patch("grobid_client.tasks.process_pdf_with_grobid")
    def test_task_full_execution_from_mock_xml(self, mock_grobid):
        """Verify complete metadata population, Reference creation, and Document provenance update."""
        fixture_path = os.path.join(os.path.dirname(__file__), 'test_data', 'sample_tei.xml')
        with open(fixture_path, 'r', encoding='utf-8') as f:
            mock_grobid.return_value = f.read()

        doc = Document.objects.create(
            title="Test Robotics Document",
            file=SimpleUploadedFile("robotics.pdf", b"%PDF-1.4 valid test pdf")
        )

        task_extract_grobid_metadata.call(doc.id)

        # Verify Reference created
        ref = Reference.objects.filter(document=doc).first()
        self.assertIsNotNone(ref)
        self.assertEqual(ref.title, "Autonomous Robotics for Indoor Firefighting Search and Rescue")
        self.assertIn("Juan Talavera", ref.authors)
        self.assertEqual(ref.year, "2023")

        # Verify Document provenance propagated
        doc.refresh_from_db()
        self.assertIn("Juan Talavera", doc.author)
        self.assertEqual(doc.source_url, "https://doi.org/10.1002/rob.22150")
        self.assertTrue(len(doc.citation_text) > 0)

        # Verify Citations extracted into bibliography
        citations = Citation.objects.filter(source_reference=ref)
        self.assertTrue(citations.count() >= 2)

    def test_real_firefighting_glossary_file_skipped(self):
        """Ensure real non-PDF firefighting text files from test_data/ are safely rejected."""
        glossary_path = os.path.join(os.path.dirname(__file__), 'test_data', 'FirefightingGlossary.txt')
        self.assertTrue(os.path.exists(glossary_path), "FirefightingGlossary.txt fixture must exist")
        with open(glossary_path, 'rb') as f:
            content = f.read()

        doc = Document.objects.create(
            title="Firefighting Glossary Knowledge",
            file=SimpleUploadedFile("FirefightingGlossary.txt", content)
        )
        res = task_extract_grobid_metadata.call(doc.id)
        self.assertIn("is not a PDF. Grobid processing skipped", res)

    @patch("grobid_client.tasks.process_pdf_with_grobid")
    def test_unstructured_catalog_or_flyer_tei_gracefully_handled(self, mock_grobid):
        """Non-academic PDFs (such as equipment catalogs) producing sparse TEI are handled without crash."""
        # Simulate sparse TEI generated by Grobid for commercial/equipment flyers (e.g. NAFFCO catalog)
        sparse_tei = """<TEI xmlns="http://www.tei-c.org/ns/1.0">
            <teiHeader>
                <fileDesc>
                    <titleStmt>
                        <title level="a" type="main"/>
                    </titleStmt>
                    <sourceDesc>
                        <biblStruct/>
                    </sourceDesc>
                </fileDesc>
            </teiHeader>
            <text>
                <body>
                    <p>NAFFCO Fire Fighting Equipment and Safety Solutions Catalog.</p>
                </body>
            </text>
        </TEI>"""
        mock_grobid.return_value = sparse_tei

        doc = Document.objects.create(
            title="NAFFCO-SAFETY-EQUIPMENTS",
            file=SimpleUploadedFile("NAFFCO-SAFETY-EQUIPMENTS.pdf", b"%PDF-1.4 dummy catalog")
        )
        res = task_extract_grobid_metadata.call(doc.id)
        self.assertIn("Grobid extraction complete for NAFFCO-SAFETY-EQUIPMENTS", res)

        ref = Reference.objects.filter(document=doc).first()
        self.assertIsNotNone(ref)
        # Should gracefully fall back to doc title
        self.assertEqual(ref.title, "NAFFCO-SAFETY-EQUIPMENTS")
        self.assertEqual(ref.authors, "")
        self.assertEqual(Citation.objects.filter(source_reference=ref).count(), 0)

    def test_extract_grobid_figures_and_mentions(self):
        """Test extraction of <figure> elements, graphic coords, captions, and context mentions."""
        from grobid_client.tasks import extract_grobid_figures, grobid_tei_to_semantic_chunks
        
        sample_tei = """<TEI xmlns="http://www.tei-c.org/ns/1.0">
            <text>
                <body>
                    <div>
                        <head>Experimental Setup</head>
                        <p>We benchmark sensor ranging accuracy under extreme particulate loading across multiple trials.</p>
                        <p>As demonstrated in <ref type="figure" target="#fig_0">Figure 4</ref>, optical backscatter degrades LiDAR beyond 150C.</p>
                        <figure xml:id="fig_0">
                            <head>Figure 4:</head>
                            <label>4</label>
                            <figDesc>Ranging error of 77-GHz FMCW radar and 3D LiDAR in dense smoke.</figDesc>
                            <graphic coords="1,100.0,200.0,300.0,150.0" type="bitmap"/>
                        </figure>
                    </div>
                </body>
            </text>
        </TEI>"""
        
        figs = extract_grobid_figures(sample_tei, document_title="LiDAR Study")
        self.assertEqual(len(figs), 1)
        fig = figs[0]
        self.assertEqual(fig.metadata["chunk_type"], "figure")
        self.assertEqual(fig.metadata["figure_label"], "Figure 4")
        self.assertEqual(fig.metadata["coords"], "1,100.0,200.0,300.0,150.0")
        self.assertIn("[Figure 4] Ranging error of 77-GHz FMCW radar", fig.page_content)
        self.assertIn("Discussion Context: As demonstrated in Figure 4", fig.page_content)

        # Test composite grobid_tei_to_semantic_chunks includes both text and figure chunks
        chunks = grobid_tei_to_semantic_chunks(sample_tei, document_title="LiDAR Study")
        self.assertTrue(len(chunks) >= 2)
        fig_in_chunks = [c for c in chunks if c.metadata.get("chunk_type") == "figure"]
        self.assertEqual(len(fig_in_chunks), 1)

    def test_pypdfium2_crop_pdf_figure(self):
        """Test deterministic high-DPI rendering and figure cropping using pypdfium2."""
        import tempfile
        from reportlab.pdfgen import canvas
        from reportlab.lib import colors
        from PIL import Image
        from background_resources.image_processing import crop_pdf_figure, parse_grobid_coords

        coords = parse_grobid_coords("1,50.5,100.2,200.0,150.0")
        self.assertEqual(coords, (1, 50.5, 100.2, 200.0, 150.0))

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = os.path.join(tmpdir, "test_doc.pdf")
            c = canvas.Canvas(pdf_path, pagesize=(612, 792))
            c.drawString(100, 700, "Header text on page 1")
            c.setFillColor(colors.red)
            c.rect(100, 400, 300, 200, fill=1)
            c.showPage()
            c.save()

            out_png = os.path.join(tmpdir, "fig_crop.png")
            res = crop_pdf_figure(pdf_path, "1,100.0,192.0,300.0,200.0", out_png, dpi=150)
            self.assertIsNotNone(res)
            self.assertTrue(os.path.exists(out_png))

            with Image.open(out_png) as img:
                self.assertGreater(img.width, 100)
                self.assertGreater(img.height, 100)

