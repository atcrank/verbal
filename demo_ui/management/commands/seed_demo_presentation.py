import os
import json
import logging
from pathlib import Path

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.core import serializers
from background_resources.models import Document, RAGChunk, GrobidReadingStrategy
from llm_api.models import Conversation, PromptResponseLog
from grips.models import Domain, ConceptNode, KnowledgeEdge
from llm_api.apps import service_registry

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = "Seeds the database with firefighting robotics papers, conversation branches, and Grips nodes for live demo presentations."

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("=== Seeding Reason Demo Presentation Data ==="))
        User = get_user_model()
        user, _ = User.objects.get_or_create(
            username="presenter",
            defaults={"email": "presenter@example.com", "is_staff": True, "is_superuser": True}
        )
        if not user.has_usable_password():
            user.set_password("presenter123")
            user.save()

        # 1. Load Firefighting RAG Chunks fixture if available
        fixture_path = Path(__file__).resolve().parent.parent.parent / "demo_ui_trials" / "fixtures" / "firefighting_chunks.json"
        if fixture_path.exists():
            self.stdout.write(f"Loading firefighting papers fixture from {fixture_path.name}...")
            with open(fixture_path, "r", encoding="utf-8") as f:
                raw_json = f.read()
                for obj in serializers.deserialize("json", raw_json):
                    obj.save()
            self.stdout.write(self.style.SUCCESS(f"Loaded documents and semantic RAG chunks."))
            
            # Reindex chunks in vector store
            rag_service = service_registry.rag_service
            rag_service.index_unindexed_chunks()
        else:
            self.stdout.write(self.style.WARNING(f"Fixture {fixture_path.name} not found yet. Proceeding with conversation and Grips seed."))

        # 2. Seed Structured Firefighting Study Design Conversation with Branches
        conv, _ = Conversation.objects.get_or_create(
            user=user,
            title="Firefighting Robotics & Drone Deployment"
        )
        
        # Turn 1: Root comparison
        log1, _ = PromptResponseLog.objects.get_or_create(
            conversation=conv,
            user=user,
            user_prompt="We are designing an autonomous search and rescue protocol for high-rise building fires. What are the key sensing, localization, and communication tradeoffs between single heavy UGVs and multi-robot swarms based on recent literature?",
            defaults={
                "system_prompt": "You are a senior robotics research and study design assistant specializing in hazardous emergency interventions.",
                "generated_response": (
                    "### Comparative Architecture: Heavy UGV vs. Multi-Robot Swarm\n\n"
                    "Based on empirical findings from Talavera et al. (2023), Penders et al., and Li et al. (2023):\n\n"
                    "1. **Heavy Ground Robot (e.g., Talavera et al., 2023)**:\n"
                    "   - **Sensing Payload**: Accommodates 3D LiDAR, long-wave infrared (FLIR) thermal cameras, and active water delivery nozzles.\n"
                    "   - **Mobility**: High obstacle clearance (tracks), capable of breaching fire doors and operating in debris.\n"
                    "   - **Limitation**: Single point of failure; acoustic/thermal shielding impairs wireless propagation beyond 3-4 concrete partition walls.\n\n"
                    "2. **Multi-Robot Swarm (Penders et al.)**:\n"
                    "   - **Communication Relay**: Automatically drops 'breadcrumb' wireless relay nodes forming an opportunistic 802.11 mesh network.\n"
                    "   - **Search Efficiency**: Parallel room exploration reduces victim localization time by up to 64%.\n"
                    "   - **Limitation**: Strict payload and battery limits (<25 minutes runtime); limited capability to extinguish active flames.\n\n"
                    "Would you like to explore **Branch A: Heavy UGV with Multi-Modal SLAM** or **Branch B: Ad-Hoc Swarm Mesh Deployment**?"
                )
            }
        )

        # Fork Branch A: Heavy UGV Multi-modal SLAM
        branch_ugv, _ = Conversation.objects.get_or_create(
            user=user,
            title="[Branch A] Heavy UGV & Thermal SLAM",
            defaults={"state_tree": {"parent_conversation_id": str(conv.id)}}
        )
        PromptResponseLog.objects.get_or_create(
            conversation=branch_ugv,
            user=user,
            user_prompt="Let's focus on the heavy UGV route. How does Li et al. (2023) achieve robust SLAM and flame identification when heavy smoke causes optical cameras to fail?",
            defaults={
                "system_prompt": "You are a senior robotics research and study design assistant specializing in hazardous emergency interventions.",
                "generated_response": (
                    "### Heavy UGV Multi-Modal Perception in Zero-Visibility Smoke\n\n"
                    "Li et al. (2023) design a hybrid perception architecture tailored for dense smoke environments:\n\n"
                    "- **Cartographer 2D/3D LiDAR SLAM**: Rather than relying on visual feature points (which scatter in smoke), the system extracts submap geometry from time-of-flight LiDAR returns.\n"
                    "- **Infrared Flame Contour Segmentation**: Calibrated infrared thermal imaging captures the $3.8\\mu m - 4.5\\mu m$ wavelength band where flame emission peaks, bypassing ambient particulate attenuation.\n"
                    "- **Dynamic Target Triangulation**: The robot continuously computes distance to the flame centroid to position the water cannon nozzle within effective 5-8 meter extinguishing range."
                )
            }
        )

        # Fork Branch B: Swarm Mesh Relay
        branch_swarm, _ = Conversation.objects.get_or_create(
            user=user,
            title="[Branch B] Swarm Breadcrumb Mesh Relay",
            defaults={"state_tree": {"parent_conversation_id": str(conv.id)}}
        )
        PromptResponseLog.objects.get_or_create(
            conversation=branch_swarm,
            user=user,
            user_prompt="Let's explore the swarm mesh relay. How does Penders et al. manage breadcrumb node dropping without human intervention?",
            defaults={
                "system_prompt": "You are a senior robotics research and study design assistant specializing in hazardous emergency interventions.",
                "generated_response": (
                    "### Autonomous Breadcrumb Relay Deployment (Penders et al.)\n\n"
                    "To maintain high-fidelity communications between human firefighters and the command post:\n\n"
                    "- **RSSI Threshold Monitoring**: As lead scout robots advance into reinforced concrete structures, signal strength (RSSI) to the nearest anchor is continuously polled.\n"
                    "- **Automated Ejection**: When link margin drops below $-75\\text{ dBm}$, a specialized carousel mechanism deploys a compact relay node to the floor.\n"
                    "- **Mesh Auto-Reconfiguration**: The ad-hoc routing protocol dynamically updates routing tables, ensuring redundant, multi-hop transmission for video and sensor telemetry."
                )
            }
        )

        # 3. Seed Grips Knowledge Graph Domain
        domain, _ = Domain.objects.get_or_create(
            name="Firefighting Robotics",
            defaults={"description": "Ontology of autonomous robotics, sensor payloads, and swarm tactics in hazardous emergency interventions."}
        )

        root_node, _ = ConceptNode.objects.get_or_create(
            domain=domain,
            title="Search and Rescue Robotics",
            slug="search-and-rescue-robotics",
            defaults={
                "narrative_content": "The application of unmanned ground, aerial, and amphibious vehicles to locate and extract victims from hazardous emergency disaster environments."
            }
        )

        slam_node, _ = ConceptNode.objects.get_or_create(
            domain=domain,
            title="Thermal SLAM & Flame Detection",
            slug="thermal-slam-flame-detection",
            defaults={
                "narrative_content": "Simultaneous Localization and Mapping combining LiDAR geometric point clouds with long-wave infrared thermography for navigation through smoke."
            }
        )

        swarm_node, _ = ConceptNode.objects.get_or_create(
            domain=domain,
            title="Ad-Hoc Swarm Mesh Network",
            slug="ad-hoc-swarm-mesh-network",
            defaults={
                "narrative_content": "Self-healing wireless communication topologies formed by mobile robotic nodes dynamically routing telemetry across RF-attenuating building materials."
            }
        )

        # Unelaborated Stub node (for testing or presenting "Fill Stub")
        stub_node, _ = ConceptNode.objects.get_or_create(
            domain=domain,
            title="Breadcrumb Sensor Node",
            slug="breadcrumb-sensor-node",
            defaults={
                "narrative_content": ""  # Intentionally empty to represent an unelaborated stub
            }
        )

        # Relations
        KnowledgeEdge.objects.get_or_create(
            source=root_node,
            target=slam_node,
            relationship_type=KnowledgeEdge.RelationshipTypes.INCLUDES,
            defaults={"justification": "Autonomous SAR comprises thermal SLAM perception."}
        )
        KnowledgeEdge.objects.get_or_create(
            source=root_node,
            target=swarm_node,
            relationship_type=KnowledgeEdge.RelationshipTypes.INCLUDES,
            defaults={"justification": "Autonomous SAR systems employ ad-hoc swarm communications."}
        )
        KnowledgeEdge.objects.get_or_create(
            source=swarm_node,
            target=stub_node,
            relationship_type=KnowledgeEdge.RelationshipTypes.DEPENDS_ON,
            defaults={"justification": "Swarm mesh connectivity depends on dropped breadcrumb sensor relays."}
        )

        self.stdout.write(self.style.SUCCESS("Seeded Grips Domain and Concept Nodes (including unelaborated stub)."))
        self.stdout.write(self.style.SUCCESS("\nDemo presentation setup complete!"))
        self.stdout.write(f"Presenter credentials: username='presenter' password='presenter123'")
        self.stdout.write(f"Chat UI: http://localhost:8000/demo/")
        self.stdout.write(f"Grips Explorer: http://localhost:8000/demo/?tab=grips")
