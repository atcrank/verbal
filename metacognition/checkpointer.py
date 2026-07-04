import json
import logging
from typing import Optional, Iterator, Dict, Any, Tuple
from django.db import transaction

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    SerializerProtocol,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

logger = logging.getLogger(__name__)


class DjangoCheckpointer(BaseCheckpointSaver):
    """
    LangGraph checkpointer that stores state in the Django `AgentCheckpoint` model.
    """

    def __init__(self, serializer: Optional[SerializerProtocol] = None):
        super().__init__(serde=serializer or JsonPlusSerializer())

    def get_tuple(self, config: dict) -> Optional[CheckpointTuple]:
        """
        Get a checkpoint tuple from the database.
        
        This fetches the latest checkpoint for a thread, or a specific checkpoint
        if `checkpoint_id` is provided in the config.
        """
        from .models import AgentCheckpoint
        
        thread_id = config["configurable"]["thread_id"]
        checkpoint_id = config["configurable"].get("checkpoint_id")
        
        try:
            if checkpoint_id:
                record = AgentCheckpoint.objects.get(thread_id=thread_id, checkpoint_id=checkpoint_id)
            else:
                # Get the most recent checkpoint for this thread
                record = AgentCheckpoint.objects.filter(thread_id=thread_id).first()
                
            if not record:
                return None
                
            import base64
            state_data_val = record.state_json["data"]
            meta_data_val = record.metadata_json["data"]
            
            state_data_bytes = base64.b64decode(state_data_val) if isinstance(state_data_val, str) else state_data_val
            meta_data_bytes = base64.b64decode(meta_data_val) if isinstance(meta_data_val, str) else meta_data_val
            
            checkpoint = self.serde.loads_typed((record.state_json["type"], state_data_bytes))
            metadata = self.serde.loads_typed((record.metadata_json["type"], meta_data_bytes))
            
            # Reconstruct the config with the found checkpoint_id
            found_config = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_id": record.checkpoint_id,
                }
            }
            
            return CheckpointTuple(
                config=found_config,
                checkpoint=checkpoint,
                metadata=metadata,
                parent_config={
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_id": record.parent_id,
                    }
                } if record.parent_id else None
            )
            
        except AgentCheckpoint.DoesNotExist:
            return None
        except Exception as e:
            logger.error(f"Error retrieving checkpoint: {e}")
            return None

    def list(
        self,
        config: Optional[dict],
        *,
        filter: Optional[dict] = None,
        before: Optional[dict] = None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointTuple]:
        """
        List checkpoints from the database.
        """
        from .models import AgentCheckpoint
        
        if not config or "configurable" not in config or "thread_id" not in config["configurable"]:
            return iter([])
            
        thread_id = config["configurable"]["thread_id"]
        
        qs = AgentCheckpoint.objects.filter(thread_id=thread_id)
        
        if before and "configurable" in before and "checkpoint_id" in before["configurable"]:
            # A bit tricky to filter by ID which is a string timestamp in LangGraph, 
            # but we can filter by the created_at timestamp if we mapped it, or just 
            # string comparison if the IDs are monotonic
            qs = qs.filter(checkpoint_id__lt=before["configurable"]["checkpoint_id"])
            
        if limit:
            qs = qs[:limit]
            
        for record in qs:
            import base64
            state_data_val = record.state_json["data"]
            meta_data_val = record.metadata_json["data"]
            
            state_data_bytes = base64.b64decode(state_data_val) if isinstance(state_data_val, str) else state_data_val
            meta_data_bytes = base64.b64decode(meta_data_val) if isinstance(meta_data_val, str) else meta_data_val
            
            checkpoint = self.serde.loads_typed((record.state_json["type"], state_data_bytes))
            metadata = self.serde.loads_typed((record.metadata_json["type"], meta_data_bytes))
            
            yield CheckpointTuple(
                config={
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_id": record.checkpoint_id,
                    }
                },
                checkpoint=checkpoint,
                metadata=metadata,
                parent_config={
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_id": record.parent_id,
                    }
                } if record.parent_id else None
            )

    def put(self, config: dict, checkpoint: Checkpoint, metadata: CheckpointMetadata, new_versions: Any) -> dict:
        """
        Save a checkpoint to the database.
        """
        from .models import AgentCheckpoint
        
        thread_id = config["configurable"]["thread_id"]
        checkpoint_id = checkpoint["id"]
        parent_id = config["configurable"].get("checkpoint_id")
        
        state_type, state_data_bytes = self.serde.dumps_typed(checkpoint)
        meta_type, meta_data_bytes = self.serde.dumps_typed(metadata)
        
        import base64
        state_data = base64.b64encode(state_data_bytes).decode('ascii')
        meta_data = base64.b64encode(meta_data_bytes).decode('ascii')
        
        try:
            with transaction.atomic():
                AgentCheckpoint.objects.update_or_create(
                    thread_id=thread_id,
                    checkpoint_id=checkpoint_id,
                    defaults={
                        "parent_id": parent_id,
                        "state_json": {"type": state_type, "data": state_data},
                        "metadata_json": {"type": meta_type, "data": meta_data},
                    }
                )
        except Exception as e:
            # We must swallow or handle exception, but the signature doesn't say
            # Just let it bubble up
            raise
            
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_id": checkpoint_id,
            }
        }

    def put_writes(self, config, writes, task_id, task_path=""):
        """
        Store intermediate writes for a task.
        For simplicity in this synchronous Django runner, we can ignore this or implement a separate model if needed.
        LangGraph requires this to be implemented (not raise NotImplementedError).
        """
        pass
