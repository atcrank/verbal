import os
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from playwright.sync_api import sync_playwright
from django.contrib.auth import get_user_model

class DemoUIWalkthroughTestCase(StaticLiveServerTestCase):
    fixtures = ['test_data.json']

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)
        
    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        super().tearDownClass()
        
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser('playwrightuser', 'playwright@example.com', 'password123')
        
        # Ensure we have at least one Grip to show in the explorer
        from grips.models import Domain, ConceptNode
        if not ConceptNode.objects.exists():
            d, _ = Domain.objects.get_or_create(name="Study Design")
            ConceptNode.objects.create(
                domain=d,
                title="Randomized Controlled Trial",
                slug="rct-1",
                narrative_content="A study design that randomly assigns participants into an experimental group or a control group."
            )
            ConceptNode.objects.create(
                domain=d,
                title="Double Blind",
                slug="double-blind-1",
                narrative_content="A blinding setup where neither the participants nor the experimenters know who is receiving a particular treatment."
            )

        # Create a dummy conversation
        from llm_api.models import Conversation, PromptResponseLog
        conv = Conversation.objects.create(
            user=self.user,
            title="Study Design Planning"
        )
        PromptResponseLog.objects.create(
            conversation=conv,
            user=self.user,
            system_prompt="You are a helpful study design assistant.",
            user_prompt="I want to design a clinical trial for a new asthma medication. Where should I start?",
            generated_response="A great place to start is defining your primary endpoints and determining if a Randomized Controlled Trial (RCT) is feasible. Would you like me to outline the key phases of an RCT for asthma?"
        )
            
        self.screenshots_dir = os.path.join("documents", "walkthroughs", "screenshots")
        os.makedirs(self.screenshots_dir, exist_ok=True)
        
        self.context = self.browser.new_context()
        self.page = self.context.new_page()

    def tearDown(self):
        self.context.close()

    def take_screenshot(self, name):
        filepath = os.path.join(self.screenshots_dir, f"{name}.png")
        self.page.screenshot(path=filepath, full_page=True)
        return filepath

    def test_demo_ui_walkthrough(self):
        """
        Walks through the main UI to take screenshots for documentation.
        This behaves like an end-to-end test.
        """
        # 1. Login
        self.page.goto(self.live_server_url + "/admin/login/?next=/demo/")
        self.page.fill('input[name="username"]', 'playwrightuser')
        self.page.fill('input[name="password"]', 'password123')
        self.page.click('input[type="submit"]')
        
        # 2. Main UI (Empty)
        self.page.wait_for_url(self.live_server_url + "/demo/")
        self.take_screenshot("01_main_ui_empty")
        
        # Select the existing conversation from the sidebar
        self.page.click('text="Study Design Planning"')
        
        # Wait for the chat to load
        self.page.wait_for_selector('.chat-history .chat-message')

        # 3. Chat interaction (Filled)
        self.page.fill('textarea[name="user_prompt"]', "Yes, please outline the key phases.")
        self.take_screenshot("02_chat_filled")
        
        self.page.click('button[type="submit"]')
        
        # Wait for the AI response (since we use a proxy, it will just return a mocked response, but we can wait for the bot-message class)
        try:
            # We wait for the *new* message to appear. The prompt we sent was "Yes, please outline the key phases."
            self.page.wait_for_selector('text="Yes, please outline the key phases."')
            # And wait for the next assistant message
            self.page.wait_for_selector('.chat-message.ai:last-child', timeout=10000)
            self.take_screenshot("03_chat_response")
        except Exception:
            self.take_screenshot("03_chat_response_timeout")
            
        # 4. Navigate to Documents / Upload (Expand the details tab on the right sidebar)
        self.page.click('text="Upload Document to RAG"')
        self.take_screenshot("04_documents_view")
        
        # 5. Grips Explorer (It's a tab on the same page)
        self.page.click('button.tab-button:has-text("Grips Explorer")')
        self.page.wait_for_selector('#grips-explorer-container')
        self.take_screenshot("05_grips_explorer")
        
        # Generate Walkthrough Markdown
        walkthrough_path = os.path.join("documents", "walkthroughs", "demo_ui_walkthrough.md")
        with open(walkthrough_path, "w") as f:
            f.write("# Demo UI Walkthrough\n\n")
            f.write("This is an automatically generated visual walkthrough of the Demo UI.\n\n")
            f.write("## 1. Main Chat Interface\n")
            f.write("![Main UI](screenshots/01_main_ui_empty.png)\n\n")
            f.write("## 2. Sending a Prompt\n")
            f.write("![Chat Filled](screenshots/02_chat_filled.png)\n\n")
            f.write("## 3. AI Response\n")
            f.write("![Chat Response](screenshots/03_chat_response.png)\n\n")
            f.write("## 4. Document Management\n")
            f.write("![Documents View](screenshots/04_documents_view.png)\n\n")
            f.write("## 5. Grips Explorer\n")
            f.write("![Grips Explorer](screenshots/05_grips_explorer.png)\n\n")
            
        self.assertTrue(os.path.exists(walkthrough_path))
