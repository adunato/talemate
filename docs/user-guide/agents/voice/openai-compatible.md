# OpenAI Compatible

!!! info "New in 0.37.0"
    The OpenAI Compatible TTS backend lets you connect to any server that exposes an OpenAI-style `/v1/audio/speech` endpoint.

Use this backend to connect Talemate to any TTS service that implements the OpenAI speech API format, such as vLLM, LocalAI, Speaches, and other self-hosted or third-party services that mirror the same endpoint shape.

Enable the **OpenAI Compatible** API in the Voice agent's [Enabled APIs](settings.md#enabled-apis) setting before configuring it.

![OpenAI Compatible selected in the Enabled APIs list](/talemate/img/0.37.0/voice-openai-compatible-enabled-apis.png)

## Settings

![OpenAI Compatible TTS settings](/talemate/img/0.37.0/voice-openai-compatible-settings.png)

##### API Base URL

Base URL of the OpenAI-compatible TTS server, including the `/v1` path.

Default: `http://localhost:8000/v1`

The agent will report "API URL not set" and will not generate audio until this value is provided.

##### API Key

API key for the server. Leave empty if your server does not require authentication &mdash; Talemate will send a placeholder value so the underlying OpenAI client accepts the request.

##### Model

Model identifier sent with each request. Some servers ignore this field and always use the model they have loaded; others route requests based on this value. Check your server's documentation.

Default: `tts-1`

##### Chunk size

Split text into chunks of this size before sending to the server. Smaller values increase responsiveness at the cost of losing context between chunks (inflection, pacing, etc.). `0` disables chunking.

Default: `512`. Range: `0`&ndash;`2048`, in steps of `64`.

## Adding Voices

Voices for this backend are **not** auto-discovered. You need to add each voice manually to the [Voice Library](voice-library.md) using a voice identifier supported by your server.

![Adding an OpenAI Compatible voice in the Voice Library](/talemate/img/0.37.0/voice-openai-compatible-add-voice.png)

1. Open the Voice Library from the main application bar.
2. Click **:material-plus: New**.
3. Select **OpenAI Compatible** as the provider.
4. Fill in the voice:

    - **Label** &mdash; Display name shown in Talemate (e.g. "Narrator - Deep Male").
    - **Provider ID** &mdash; The voice identifier your server expects (e.g. `alloy`, `echo`, or a custom voice name defined by the server).
    - **Tags** &mdash; Optional descriptive tags for organization and filtering.

Refer to your TTS server's documentation for the list of supported voice identifiers. Once added, the voice can be [assigned to characters](voice-library.md#character-voice-assignment) or used as the [narrator voice](settings.md#narrator-voice).

## Troubleshooting

**Connection errors**: Verify the base URL is correct (including the `/v1` path) and that the server is running and reachable from Talemate.

**Empty or silent audio**: Confirm your server has a TTS model loaded and that the voice ID you sent is valid for that model. Some servers fall back silently when given an unknown voice.

**Authentication errors**: If your server requires an API key, make sure it is set in the settings above. Otherwise leave the field empty.

See also the general [TTS Troubleshooting Guide](troubleshooting.md).
