from django.test import TestCase, Client
from django.contrib.auth.models import User, Group
from unittest.mock import patch
from .models import Project, Workshop, WorkshopSession, WhiteboardCard, WhiteboardCluster, ConversationMember
from .clustering import (
    IdeaClusteringPlan, IdeaClusterItem,
    CausalGraphExtractionPlan, CausalFactorItem,
    cluster_whiteboard_cards, extract_causal_factors_from_session,
    export_whiteboard_markdown_summary
)
from .events import format_datastar_sse, stream_whiteboard_events


class WorkOrganisationPermissionTests(TestCase):
    """
    Tests group-based queryset scoping and the 4 access/anonymity modes.
    """
    def setUp(self):
        self.group_a = Group.objects.create(name="Team Alpha")
        self.group_b = Group.objects.create(name="Team Beta")

        self.user_a = User.objects.create_user(username="alice", password="password123")
        self.user_a.groups.add(self.group_a)

        self.user_b = User.objects.create_user(username="bob", password="password123")
        self.user_b.groups.add(self.group_b)

        self.superuser = User.objects.create_superuser(username="admin", password="password123")

        # Project A (Restricted to Group A)
        self.project_a = Project.objects.create(
            name="Project Alpha",
            description="Alpha private workspace",
            created_by=self.user_a,
            is_public=False
        )
        self.project_a.groups.add(self.group_a)

        # Workshop A under Project A
        self.workshop_a = Workshop.objects.create(
            project=self.project_a,
            name="Alpha Workshop",
            created_by=self.user_a
        )

        # Session 1: Restricted Tracked
        self.session_tracked = WorkshopSession.objects.create(
            workshop=self.workshop_a,
            title="Tracked Session",
            access_mode="RESTRICTED_TRACKED"
        )

        # Session 2: Restricted Anonymized in UI
        self.session_anon_ui = WorkshopSession.objects.create(
            workshop=self.workshop_a,
            title="Anon UI Session",
            access_mode="RESTRICTED_ANONYMIZED_UI"
        )

        # Session 3: Restricted Anonymized in DB
        self.session_anon_db = WorkshopSession.objects.create(
            workshop=self.workshop_a,
            title="Anon DB Session",
            access_mode="RESTRICTED_ANONYMIZED_DB"
        )

        # Project B (Public)
        self.project_public = Project.objects.create(
            name="Public Brainstorming",
            created_by=self.user_b,
            is_public=True
        )
        self.workshop_public = Workshop.objects.create(
            project=self.project_public,
            name="Public Workshop",
            created_by=self.user_b
        )
        self.session_public = WorkshopSession.objects.create(
            workshop=self.workshop_public,
            title="Public Session",
            access_mode="PUBLIC_OPTIONAL_USER"
        )

    def test_project_group_scoping(self):
        # Alice in Team Alpha sees Project A
        alice_projects = Project.objects.for_user(self.user_a)
        self.assertIn(self.project_a, alice_projects)
        self.assertIn(self.project_public, alice_projects)

        # Bob in Team Beta cannot see Project A, but sees Public Project
        bob_projects = Project.objects.for_user(self.user_b)
        self.assertNotIn(self.project_a, bob_projects)
        self.assertIn(self.project_public, bob_projects)

        # Superuser sees all projects
        admin_projects = Project.objects.for_user(self.superuser)
        self.assertIn(self.project_a, admin_projects)
        self.assertIn(self.project_public, admin_projects)

    def test_session_group_scoping(self):
        alice_sessions = WorkshopSession.objects.for_user(self.user_a)
        self.assertIn(self.session_tracked, alice_sessions)

        bob_sessions = WorkshopSession.objects.for_user(self.user_b)
        self.assertNotIn(self.session_tracked, bob_sessions)
        self.assertIn(self.session_public, bob_sessions)

    def test_card_creation_access_modes_api(self):
        client = Client()
        client.force_login(self.user_a)

        # 1. Test RESTRICTED_TRACKED
        res1 = client.post("/api/whiteboard/cards/", data={
            "session_id": self.session_tracked.id,
            "text": "Idea from Alice",
            "card_type": "idea"
        }, content_type="application/json")
        self.assertEqual(res1.status_code, 200)
        card1 = WhiteboardCard.objects.get(id=res1.json()["card_id"])
        self.assertEqual(card1.author, self.user_a)
        self.assertEqual(card1.author_alias, "")

        # 2. Test RESTRICTED_ANONYMIZED_UI
        res2 = client.post("/api/whiteboard/cards/", data={
            "session_id": self.session_anon_ui.id,
            "text": "Secret idea from Alice",
            "card_type": "idea"
        }, content_type="application/json")
        self.assertEqual(res2.status_code, 200)
        card2 = WhiteboardCard.objects.get(id=res2.json()["card_id"])
        self.assertEqual(card2.author, self.user_a)
        self.assertTrue(card2.author_alias.startswith("Participant #"))

        # 3. Test RESTRICTED_ANONYMIZED_DB
        res3 = client.post("/api/whiteboard/cards/", data={
            "session_id": self.session_anon_db.id,
            "text": "Fully anonymous idea",
            "card_type": "idea"
        }, content_type="application/json")
        self.assertEqual(res3.status_code, 200)
        card3 = WhiteboardCard.objects.get(id=res3.json()["card_id"])
        self.assertIsNone(card3.author)
        self.assertEqual(card3.author_alias, "Anonymous")

    def test_conversation_member_roles(self):
        from llm_api.models import Conversation
        conv = Conversation.objects.create(user=self.user_a, title="Collaboration")
        mem1 = ConversationMember.objects.create(conversation=conv, user=self.user_a, role='owner')
        mem2 = ConversationMember.objects.create(conversation=conv, user=self.user_b, role='editor', display_alias='BobCollaborator')

        self.assertEqual(conv.members.count(), 2)
        self.assertEqual(mem1.role, 'owner')
        self.assertEqual(mem2.display_alias, 'BobCollaborator')


class WhiteboardSSEEventTests(TestCase):
    """
    Tests real-time Datastar SSE formatting and event generation.
    """
    def test_format_datastar_sse_signal(self):
        res = format_datastar_sse("card_added", {"card_id": 42, "text": "New note"})
        self.assertIn("event: card_added", res)
        self.assertIn('"card_id": 42', res)

    def test_format_datastar_sse_fragment(self):
        html = '<div id="card-42" class="card">Hello</div>'
        res = format_datastar_sse("merge", {}, fragment_html=html)
        self.assertIn("event: datastar-merge-fragments", res)
        self.assertIn("data: fragments <div id=\"card-42\"", res)

    @patch('work_organisation.events.redis_client', None)
    def test_stream_whiteboard_events_fallback_when_redis_offline(self):
        gen = stream_whiteboard_events(session_id=123)
        first_event = next(gen)
        self.assertIn("Redis broker offline", first_event)


class WhiteboardClusteringTests(TestCase):
    """
    Tests AI idea clustering, causal factor extraction, and markdown export formatters.
    """
    def setUp(self):
        self.user = User.objects.create_user(username="researcher", password="password123")
        self.project = Project.objects.create(name="AI Lab", created_by=self.user, is_public=True)
        self.workshop = Workshop.objects.create(project=self.project, name="System Dynamics", objective="Model feedback loops", created_by=self.user)
        self.session = WorkshopSession.objects.create(workshop=self.workshop, title="Causal Loop Ideation", access_mode="RESTRICTED_TRACKED")

        # Seed cards
        self.card1 = WhiteboardCard.objects.create(session=self.session, text="Improve vector retrieval speed", card_type="idea", author=self.user)
        self.card2 = WhiteboardCard.objects.create(session=self.session, text="Add HNSW indexing for embeddings", card_type="idea", author=self.user)
        self.card3 = WhiteboardCard.objects.create(session=self.session, text="Human safety in the loop for tools", card_type="idea", author=self.user)
        self.card4 = WhiteboardCard.objects.create(session=self.session, text="Require approval before running dynamic code", card_type="idea", author=self.user)

    @patch('llm_api.ai_service.AIService.generate_outline')
    def test_cluster_whiteboard_cards(self, mock_generate):
        # Mock structured LLM response
        mock_generate.return_value = IdeaClusteringPlan(
            reasoning="Grouped into Performance and Security themes.",
            clusters=[
                IdeaClusterItem(
                    title="Vector Performance Optimization",
                    summary="Speeding up retrieval via HNSW and pgvector.",
                    color="#10B981",
                    card_ids=[self.card1.id, self.card2.id]
                ),
                IdeaClusterItem(
                    title="Tool Safety & Governance",
                    summary="Human-in-the-loop dynamic tool verification.",
                    color="#EF4444",
                    card_ids=[self.card3.id, self.card4.id]
                )
            ]
        )

        res = cluster_whiteboard_cards(self.session.id, user=self.user)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["clusters_created"], 2)

        # Verify cluster database records
        clusters = WhiteboardCluster.objects.filter(session=self.session)
        self.assertEqual(clusters.count(), 2)

        perf_cluster = clusters.get(title="Vector Performance Optimization")
        self.assertEqual(perf_cluster.cards.count(), 2)
        self.assertIn(self.card1, perf_cluster.cards.all())
        self.assertIn(self.card2, perf_cluster.cards.all())

        gov_cluster = clusters.get(title="Tool Safety & Governance")
        self.assertEqual(gov_cluster.cards.count(), 2)
        self.assertIn(self.card3, gov_cluster.cards.all())

    @patch('llm_api.ai_service.AIService.generate_outline')
    def test_extract_causal_factors_from_session(self, mock_generate):
        mock_generate.return_value = CausalGraphExtractionPlan(
            reasoning="Vector indexing directly drives response latency.",
            factors=[
                CausalFactorItem(
                    name="Vector Index Type",
                    state_options=["Exact Flat", "HNSW"],
                    causes=[],
                    justification="Configured in Django pgvector migration."
                ),
                CausalFactorItem(
                    name="Query Latency",
                    state_options=["Low (<50ms)", "High (>500ms)"],
                    causes=["Vector Index Type"],
                    justification="HNSW reduces search duration drastically."
                )
            ]
        )

        res = extract_causal_factors_from_session(self.session.id, user=self.user)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["factors_count"], 2)

        factor_cards = WhiteboardCard.objects.filter(session=self.session, card_type="factor")
        self.assertEqual(factor_cards.count(), 2)

    def test_export_whiteboard_markdown_summary(self):
        # Create a cluster and a question
        cluster = WhiteboardCluster.objects.create(session=self.session, title="Search Infrastructure", summary="Database enhancements")
        self.card1.cluster = cluster
        self.card1.save()

        WhiteboardCard.objects.create(session=self.session, text="What is the RAM overhead of HNSW?", card_type="question")

        md = export_whiteboard_markdown_summary(self.session.id, user=self.user)
        self.assertIn("Workshop Session Summary: Causal Loop Ideation", md)
        self.assertIn("Search Infrastructure", md)
        self.assertIn("Improve vector retrieval speed", md)
        self.assertIn("What is the RAM overhead of HNSW?", md)


class WorkOrganisationManagementAPITests(TestCase):
    """
    Tests the CRUD and quick-launch management endpoints for Projects, Workshops, and Sessions.
    """
    def setUp(self):
        self.group = Group.objects.create(name="ResearchLab")
        self.user = User.objects.create_user(username="lead_scientist", password="password123")
        self.user.groups.add(self.group)

        self.client = Client()
        self.client.force_login(self.user)

    def test_create_and_list_projects_api(self):
        # Create Project
        res = self.client.post("/api/whiteboard/projects/", data={
            "name": "Neural Networks Project",
            "description": "Exploration of deep learning architectures",
            "is_public": False,
            "group_ids": [self.group.id]
        }, content_type="application/json")
        self.assertEqual(res.status_code, 200)
        proj_data = res.json()
        self.assertEqual(proj_data["name"], "Neural Networks Project")
        self.assertEqual(proj_data["slug"], "neural-networks-project")

        # List Projects
        list_res = self.client.get("/api/whiteboard/projects/")
        self.assertEqual(list_res.status_code, 200)
        names = [p["name"] for p in list_res.json()]
        self.assertIn("Neural Networks Project", names)

    def test_create_and_list_workshops_api(self):
        project = Project.objects.create(name="Quantum AI", created_by=self.user, is_public=True)
        res = self.client.post("/api/whiteboard/workshops/", data={
            "project_id": project.id,
            "name": "QML Workshop",
            "description": "Quantum Machine Learning exploration",
            "objective": "Design quantum circuit architectures"
        }, content_type="application/json")
        self.assertEqual(res.status_code, 200)
        ws_data = res.json()
        self.assertEqual(ws_data["name"], "QML Workshop")

        list_res = self.client.get(f"/api/whiteboard/workshops/?project_id={project.id}")
        self.assertEqual(list_res.status_code, 200)
        self.assertEqual(len(list_res.json()), 1)
        self.assertEqual(list_res.json()[0]["name"], "QML Workshop")

    def test_quick_launch_session_and_auto_provision_conversation(self):
        project = Project.objects.create(name="Robotics", created_by=self.user, is_public=True)
        workshop = Workshop.objects.create(project=project, name="Autonomous Nav", created_by=self.user)

        res = self.client.post("/api/whiteboard/sessions/new/", data={
            "workshop_id": workshop.id,
            "title": "Sensor Fusion Whiteboard",
            "session_type": "whiteboard",
            "access_mode": "RESTRICTED_TRACKED"
        }, content_type="application/json")
        self.assertEqual(res.status_code, 200)
        session_data = res.json()

        session = WorkshopSession.objects.get(id=session_data["id"])
        self.assertIsNotNone(session.conversation)
        self.assertEqual(session.conversation.title, "Autonomous Nav: Sensor Fusion Whiteboard")

        # Verify owner membership was created
        membership = ConversationMember.objects.filter(conversation=session.conversation, user=self.user).first()
        self.assertIsNotNone(membership)
        self.assertEqual(membership.role, "owner")

    def test_get_session_detail_api(self):
        project = Project.objects.create(name="Cognitive Science", created_by=self.user, is_public=True)
        workshop = Workshop.objects.create(project=project, name="Memory Models", created_by=self.user)
        session = WorkshopSession.objects.create(workshop=workshop, title="Working Memory", access_mode="RESTRICTED_ANONYMIZED_UI")

        cluster = WhiteboardCluster.objects.create(session=session, title="Buffer Storage", color="#3B82F6")
        card = WhiteboardCard.objects.create(session=session, text="7 +/- 2 item limit", cluster=cluster, author=self.user, author_alias="Participant #10")

        res = self.client.get(f"/api/whiteboard/sessions/{session.id}/")
        self.assertEqual(res.status_code, 200)
        data = res.json()

        self.assertEqual(data["title"], "Working Memory")
        self.assertEqual(len(data["clusters"]), 1)
        self.assertEqual(data["clusters"][0]["title"], "Buffer Storage")
        self.assertEqual(len(data["cards"]), 1)
        self.assertEqual(data["cards"][0]["text"], "7 +/- 2 item limit")
        self.assertEqual(data["cards"][0]["author_alias"], "Participant #10")


class WorkOrganisationAdminTests(TestCase):
    """
    Tests Django Admin queryset scoping and helper methods.
    """
    def setUp(self):
        from django.contrib.admin.sites import AdminSite
        from work_organisation.admin import ProjectAdmin, WorkshopAdmin, WorkshopSessionAdmin

        self.site = AdminSite()
        self.group = Group.objects.create(name="Alpha")
        self.user = User.objects.create_user(username="staff_user", password="password123", is_staff=True)
        self.user.groups.add(self.group)

        self.other_user = User.objects.create_user(username="other_staff", password="password123", is_staff=True)

        self.project = Project.objects.create(name="Alpha Project", created_by=self.user)
        self.project.groups.add(self.group)

        self.other_project = Project.objects.create(name="Secret Project", created_by=self.other_user)

        self.proj_admin = ProjectAdmin(Project, self.site)

    def test_admin_project_queryset_scoping(self):
        from django.test.client import RequestFactory
        factory = RequestFactory()

        request = factory.get('/admin/work_organisation/project/')
        request.user = self.user

        qs = self.proj_admin.get_queryset(request)
        self.assertIn(self.project, qs)
        self.assertNotIn(self.other_project, qs)

