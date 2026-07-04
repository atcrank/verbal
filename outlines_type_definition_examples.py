import logging
logger = logging.getLogger(__name__)

from outlines.types import JsonSchema
schema_string = {
  "title": "Hydrant",
  "type": "object",
  "properties": {
    "location_name": {
      "type": "string",
      "description": "name of the hydrant"
    }
  },
  "required": [
    "location_name"
  ]
}

hydrant_json_def = JsonSchema(schema_string)

logger.info(hydrant_json_def)

schema_string = {
  "title": "Hydrant",
  "type": "object",
  "properties": {
    "location_name": {
      "type": "string",
      "description": "name of the hydrant"
    }
  },
  "required": [
    "location_name"
  ]
}

hydrant_json_def = JsonSchema(schema_string)

logger.info(hydrant_json_def)
