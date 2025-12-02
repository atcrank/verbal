from django.test import TestCase

# Create your tests here.
from django.test import TestCase, Client
from django.contrib.auth.models import User
from unittest.mock import patch, MagicMock
from .models import PromptResponseLog


class LlmApiTests(TestCase):

    def setUp(self):
        # Create a test client and a test user
        self.client = Client()
        self.user = User.objects.create_user(
            username='testuser',
            password='password123'
        )
        self.client.login(username='testuser', password='password123')

    @patch('llm_api.api.rag_service')  # Mocks the RAG service import
    @patch('llm_api.api.ai_service')  # Mocks the AI service import
    def test_generate_response_endpoint(self, mock_ai_service, mock_rag_service):
        """
        Test the /generate_response/ endpoint.
        """
        # --- 1. Setup Mocks ---
        # Configure the mocks to return fake (but valid) data
        mock_rag_service.get_context.return_value = "This is a RAG snippet."
        mock_ai_service.generate_response.return_value = "This is the final AI response."
        mock_ai_service.clean_response.return_value = "This is the final AI response."  # Mock the (fixed) clean_response

        # --- 2. Make the API Call ---
        # Define the payload for the POST request
        payload = {
            "system_prompt": "You are a test bot.",
            "user_prompt": "Hello",
            "max_new_tokens": 50
        }

        response = self.client.post("/api/llm/generate_response/", data=payload, content_type="application/json")

        # --- 3. Assert Results ---
        # Check that the response is successful (HTTP 200)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode('utf-8'), '{"cleaned_response": "This is the final AI response."}')

        # Check that the AI service was called with the *augmented* prompt
        expected_system_prompt = (
            "You are a test bot.\n  "
            "These extracts from a local collection of authoritative documents "
            "should be used to help guide your answer:\n "
            "This is a RAG snippet.  "
        )
        mock_ai_service.generate_response.assert_called_once()
        print(mock_ai_service.generate_response.call_args)
        called_args = mock_ai_service.generate_response.call_args[1]
        self.assertEqual(called_args['messages'][0]['content'], expected_system_prompt)
        self.assertEqual(called_args['messages'][1]['content'], "Hello")

        # Check that the log was saved correctly
        self.assertEqual(PromptResponseLog.objects.count(), 1)
        log_entry = PromptResponseLog.objects.first()
        self.assertEqual(log_entry.user, self.user)
        self.assertEqual(log_entry.system_prompt, payload['system_prompt'])  # Check augmented prompt was saved
        self.assertEqual(log_entry.rag_selections, "This is a RAG snippet.")
        self.assertEqual(log_entry.generated_response, "This is the final AI response.")