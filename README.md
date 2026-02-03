# ADK to OpenAI API Middleware

A high-performance Python middleware service that converts Google Agent Development Kit (ADK) custom REST/SSE endpoints into OpenAI-compatible API format, enabling seamless integration with platforms like Dify, LangChain, and other LLM frameworks.

## Overview

This middleware acts as a protocol translation layer between ADK's custom API and the OpenAI Chat Completions API standard. It handles:

- OpenAI-compatible `/v1/chat/completions` endpoint
- Streaming and non-streaming response modes
- Multimodal content processing (images, videos, documents)
- Session management for ADK agents
- API key authentication

## Architecture

```
+----------------+     HTTP/SSE      +----------------+     HTTP/SSE      +----------------+
|                |   ---------->    |                |   ---------->    |                |
| LLM Platform   |                  | FastAPI        |                  | ADK Backend    |
| (Dify, etc.)   |                  | Middleware     |                  |                |
| OpenAI Format  |   <----------    | Protocol Layer |   <----------    | Custom API     |
+----------------+     JSON/SSE      +----------------+     JSON/SSE      +----------------+
```

## Features

### Core Capabilities

- **OpenAI API Compatibility**: Full compatibility with OpenAI Chat Completions API specification
- **Streaming Support**: SSE-based streaming responses with simulated typewriter effect
- **Multimodal Processing**:
  - Image processing (JPEG, PNG, GIF, WebP, BMP, SVG)
  - Document handling (PDF, Word, Excel, PowerPoint)
  - Video and audio file support
  - Base64 encoding and URL download
- **Session Management**: Automatic ADK session creation and lifecycle management
- **Authentication**: Optional Bearer token API key authentication
- **File Upload**: Direct file upload endpoint with validation

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/chat/completions` | POST | Create chat completion (streaming/non-streaming) |
| `/v1/models` | GET | List available ADK agent models |
| `/v1/health` | GET | Health check endpoint |
| `/upload` | POST | Upload file and convert to Base64 |

## Quick Start

### Prerequisites

- Python 3.8+
- ADK backend service running
- 2GB+ RAM recommended

### Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd adk_to_openai_api
```

2. Create a virtual environment:
```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux/Mac
source .venv/bin/activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Configure environment variables (optional):
```bash
# Create .env file
ADK_HOST=http://localhost:8000
ADK_APP_NAME=agent
PORT=8080
LOG_LEVEL=INFO
REQUIRE_API_KEY=false
```

5. Start the server:
```bash
# Development mode
python main.py

# Or with uvicorn directly
python -m uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload

# Production mode
python -m uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers 4
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ADK_HOST` | `http://localhost:8000` | ADK backend service URL |
| `ADK_APP_NAME` | `agent` | Default ADK agent/APP name |
| `PORT` | `8080` | Middleware service port |
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG/INFO/WARNING/ERROR) |
| `MAX_FILE_SIZE_MB` | `20` | Maximum file size for uploads |
| `DOWNLOAD_TIMEOUT` | `30` | URL download timeout (seconds) |
| `REQUIRE_API_KEY` | `false` | Enable API key authentication |
| `API_KEYS` | (empty) | Comma-separated list of valid API keys |

### Platform Configuration

#### Dify

1. Navigate to Settings > Model Providers
2. Add OpenAI API-compatible provider
3. Configure:
   - **API Base URL**: `http://your-middleware-host:8080/v1`
   - **API Key**: Your configured API key (if enabled)
   - **Model Name**: Matches your ADK `appName`

#### Other OpenAI-Compatible Platforms

Use the same configuration pattern with:
- Base URL: `http://localhost:8080/v1`
- API Key: As configured
- Model: Your ADK agent name

## API Usage

### Chat Completions

**Request** (curl):
```bash
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-adk-middleware-key" \
  -d '{
    "model": "agent",
    "messages": [
      {
        "role": "user",
        "content": "Hello, how can you help me?"
      }
    ],
    "stream": false
  }'
```

**Request** (with image):
```bash
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agent",
    "messages": [
      {
        "role": "user",
        "content": [
          {
            "type": "text",
            "text": "Describe this image"
          },
          {
            "type": "image_url",
            "image_url": {
              "url": "https://example.com/image.jpg"
            }
          }
        ]
      }
    ],
    "stream": true
  }'
```

**Response** (non-streaming):
```json
{
  "id": "chatcmpl-1234567890",
  "object": "chat.completion",
  "created": 1234567890,
  "model": "agent",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Hello! I'm here to help you..."
      },
      "finish_reason": "stop"
    }
  ]
}
```

**Response** (streaming):
```
data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1234567890,"model":"agent","choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}

data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1234567890,"model":"agent","choices":[{"index":0,"delta":{"content":"!"},"finish_reason":null}]}

data: [DONE]
```

### List Models

```bash
curl http://localhost:8080/v1/models
```

**Response**:
```json
{
  "object": "list",
  "data": [
    {
      "id": "agent",
      "object": "model",
      "created": 1234567890,
      "owned_by": "adk"
    }
  ]
}
```

### File Upload

```bash
curl -X POST http://localhost:8080/upload \
  -H "Authorization: Bearer sk-adk-middleware-key" \
  -F "file=@/path/to/file.jpg"
```

**Response**:
```json
{
  "success": true,
  "filename": "file.jpg",
  "mime_type": "image/jpeg",
  "base64_data": "/9j/4AAQSkZJRg...",
  "size": 12345
}
```

## Project Structure

```
adk_to_openai_api/
├── app/
│   ├── __init__.py
│   ├── main.py           # FastAPI application and endpoints
│   ├── adk_client.py     # ADK backend client
│   ├── multimodal.py     # Multimodal content processor
│   ├── models.py         # Pydantic data models
│   ├── config.py         # Configuration management
│   └── auth.py           # API key authentication
├── main.py               # Application entry point
├── requirements.txt      # Python dependencies
└── README.md            # This file
```

## How It Works

### Request Flow

1. **Receive Request**: FastAPI receives OpenAI-format request
2. **Validate**: Authentication and request validation
3. **Transform**: Convert OpenAI format to ADK format
4. **Multimodal Processing**: Download/convert images and files to Base64
5. **ADK Session**: Ensure ADK session exists for the user
6. **Call ADK**: Forward request to ADK backend
7. **Transform Response**: Convert ADK response to OpenAI format
8. **Stream**: Optionally chunk response for streaming

### Key Components

- **ADKClient** (`adk_client.py`): Handles communication with ADK backend
- **MultimodalProcessor** (`multimodal.py`): Processes images, files, and URLs
- **APIKeyAuth** (`auth.py`): Manages Bearer token authentication
- **Models** (`models.py`): Pydantic models for request/response validation

## Troubleshooting

### Common Issues

**ADK Connection Failed**
```
ERROR: ADK HTTP error: 503 - Service Unavailable
```
Solution: Verify ADK backend is running and `ADK_HOST` is correct.

**Image Processing Failed**
```
WARNING: Failed to process image URL: timeout
```
Solution: Check URL accessibility, network connectivity, or increase `DOWNLOAD_TIMEOUT`.

**Session Already Exists**
```
INFO: ADK session already exists: session_xxx
```
This is informational - the middleware handles existing sessions automatically.

### Debug Mode

Enable detailed logging:
```bash
LOG_LEVEL=DEBUG python main.py
```

Or with uvicorn:
```bash
python -m uvicorn app.main:app --log-level debug
```

## Development

### Running Tests

```bash
# Run all tests
pytest tests/

# Run with coverage
pytest --cov=app tests/
```

### Adding Features

1. **New endpoints**: Add to `app/main.py`
2. **New models**: Define in `app/models.py`
3. **ADK operations**: Extend `app/adk_client.py`
4. **Content processing**: Extend `app/multimodal.py`

## License

MIT License - see LICENSE file for details

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
