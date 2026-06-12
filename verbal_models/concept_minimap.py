import torch
import torch.nn.functional as F  # or torch.nn.cosine_similarity

def run_minimap():
    from llm_api.apps import service_registry
    ai_service = service_registry['ai_service']
    rag_service = service_registry['rag_service']

    model = ai_service.model
    embedding_layer = model.get_input_embeddings()
    embedding_weights = embedding_layer.weight.data  # matrix of embeddings

    # Get the embedding for a specific token ID
    tokenizer = ai_service.tokenizer
    token_id = tokenizer.convert_tokens_to_ids('location')
    location_vector = embedding_weights[token_id]

    for other_vector in embedding_weights:
        F.cosine_similarity(location_vector, other_vector, dim=1)

if __name__ == '__main__':
    run_minimap()