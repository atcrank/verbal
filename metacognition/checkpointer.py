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
            
            # Pending writes
            pending_writes = None
            if isinstance(record.metadata_json, dict) and "pending_writes" in record.metadata_json:
                pending_writes = []
                for w in record.metadata_json.get("pending_writes", []):
                    w_data = w.get("data")
                    w_bytes = base64.b64decode(w_data) if isinstance(w_data, str) else w_data
                    val = self.serde.loads_typed((w["type"], w_bytes))
                    pending_writes.append((w["task_id"], w["channel"], val))

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
                } if record.parent_id else None,
                pending_writes=pending_writes
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
            qs = qs.filter(checkpoint_id__lt=before["configurable"]["checkpoint_id"])
            
        if limit:
            qs = qs[:limit]
            
        for record in qs:
            import base64
            state_data_val = record.state_json.get("data")
            meta_data_val = record.metadata_json.get("data")
            
            state_data_bytes = base64.b64decode(state_data_val) if isinstance(state_data_val, str) else state_data_val
            meta_data_bytes = base64.b64decode(meta_data_val) if isinstance(meta_data_val, str) else meta_data_val
            
            checkpoint = self.serde.loads_typed((record.state_json["type"], state_data_bytes))
            metadata = self.serde.loads_typed((record.metadata_json["type"], meta_data_bytes))
            
            pending_writes = None
            if isinstance(record.metadata_json, dict) and "pending_writes" in record.metadata_json:
                pending_writes = []
                for w in record.metadata_json.get("pending_writes", []):
                    w_data = w.get("data")
                    w_bytes = base64.b64decode(w_data) if isinstance(w_data, str) else w_data
                    val = self.serde.loads_typed((w["type"], w_bytes))
                    pending_writes.append((w["task_id"], w["channel"], val))

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
                } if record.parent_id else None,
                pending_writes=pending_writes
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
                record, _ = AgentCheckpoint.objects.get_or_create(
                    thread_id=thread_id,
                    checkpoint_id=checkpoint_id,
                    defaults={
                        "parent_id": parent_id,
                        "state_json": {"type": state_type, "data": state_data},
                        "metadata_json": {"type": meta_type, "data": meta_data},
                    }
                )
                if not _:
                    record.parent_id = parent_id
                    record.state_json = {"type": state_type, "data": state_data}
                    # Preserve any pending writes already attached to this checkpoint
                    existing_meta = record.metadata_json or {}
                    new_meta = {"type": meta_type, "data": meta_data}
                    if "pending_writes" in existing_meta:
                        new_meta["pending_writes"] = existing_meta["pending_writes"]
                    record.metadata_json = new_meta
                    record.save()
        except Exception as e:
            raise
            
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_id": checkpoint_id,
            }
        }

    def put_writes(
        self,
        config: dict,
        writes: Any,
        task_id: str,
        task_path: str = "",
    ) -> None:
        """
        Store intermediate writes for a task.
        Persisted inside AgentCheckpoint.metadata_json['pending_writes'].
        """
        from .models import AgentCheckpoint
        import base64

        thread_id = config["configurable"]["thread_id"]
        checkpoint_id = config["configurable"]["checkpoint_id"]

        serialized_writes = []
        for channel, val in writes:
            w_type, w_bytes = self.serde.dumps_typed(val)
            w_data = base64.b64encode(w_bytes).decode("ascii")
            serialized_writes.append({
                "task_id": task_id,
                "task_path": task_path,
                "channel": channel,
                "type": w_type,
                "data": w_data,
            })

        try:
            with transaction.atomic():
                record, created = AgentCheckpoint.objects.get_or_create(
                    thread_id=thread_id,
                    checkpoint_id=checkpoint_id,
                    defaults={
                        "state_json": {},
                        "metadata_json": {"pending_writes": serialized_writes}
                    }
                )
                if not created:
                    meta = record.metadata_json or {}
                    current_writes = meta.get("pending_writes", [])
                    current_writes.extend(serialized_writes)
                    meta["pending_writes"] = current_writes
                    record.metadata_json = meta
                    record.save(update_fields=["metadata_json"])
        except Exception as e:
            logger.error(f"Error saving intermediate writes: {e}")

