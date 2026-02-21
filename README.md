# LLM Autocomplete for LibreOffice Writer

Real-time AI-powered ghost text autocomplete for LibreOffice Writer, similar to GitHub Copilot. Works with Anthropic Claude, OpenAI, or any compatible API including local models via Ollama.

![LibreOffice Writer with ghost text suggestion](https://img.shields.io/badge/LibreOffice-7.x%2B-green) ![License: MIT](https://img.shields.io/badge/License-MIT-blue)

## How It Works

Type in Writer, pause briefly, and a gray italic suggestion appears inline. Accept it, grab a word at a time, or dismiss it and keep typing.

| Action | Key |
|--------|-----|
| Accept entire suggestion | **Right Arrow** |
| Dismiss suggestion | **Escape** |
| Type through | Type matching characters (suggestion shrinks) |

## Install

### From Release (easiest)

1. Download `LLMAutocomplete.oxt` from [Releases](../../releases)
2. Double-click the `.oxt` file, or open LibreOffice > **Tools > Extension Manager > Add**
3. Restart LibreOffice

### From Source

```bash
git clone https://github.com/YOUR_USERNAME/libreoffice-llm-autocomplete.git
cd libreoffice-llm-autocomplete
bash build.sh
# Then install LLMAutocomplete.oxt via Extension Manager
```

## Setup

1. Open Writer, go to **View > Sidebar**
2. Click the **LLM Autocomplete** panel
3. Expand **API Settings**, enter your API key
4. Click **Save Settings**

### Get an API Key

| Provider | Get Key | Cost |
|----------|---------|------|
| **Anthropic Claude** (recommended) | [console.anthropic.com](https://console.anthropic.com) | Pay-per-use, ~$0.001/suggestion with Haiku |
| **OpenAI** | [platform.openai.com](https://platform.openai.com) | Pay-per-use |
| **Ollama** (free, local) | [ollama.com](https://ollama.com) | Free, runs on your machine |

For Ollama: set Base URL to `http://localhost:11434/v1`, model to your local model name (e.g. `llama3`). No API key needed.

### Privacy

- Your API key is stored **locally only** in `~/.llmautocomplete/settings.json`
- Text context (last 500 chars before cursor) is sent to your configured API endpoint
- **No telemetry, no data collection, no third-party servers**
- For maximum privacy, use Ollama (everything stays on your machine)

## Sidebar Panel

The sidebar has collapsible sections:

- **API Settings** (collapsed by default): API key, model, base URL, max tokens, max context
- **Debugging** (collapsed by default): sliders for all timer delays, reset button

### Timer Settings

| Setting | Default | What it does |
|---------|---------|--------------|
| Debounce | 600 ms | Wait after last keystroke before querying the API |
| Advance timer | 20 ms | Time window to suppress deferred events during type-through |
| Poll drain | 300 ms | Interval for checking the suggestion queue |
| Status poll | 500 ms | Interval for updating the sidebar status label |

## Requirements

- LibreOffice 7.x+ (Writer)
- Internet access (or local LLM via Ollama)
- API key from Anthropic, OpenAI, or compatible provider

## How It Works (Technical)

Ghost text is implemented as real characters styled with a custom `CharacterStyle` (gray italic). A `XModifyListener` detects typing, debounces, then queries the LLM API. Suggestions are inserted at the cursor position. A `XDispatchProviderInterceptor` intercepts Right Arrow (`.uno:GoRight`) to accept suggestions, while `XKeyHandler` handles Escape to dismiss.

Key technical challenges solved:
- **Style leak prevention**: `setPropertyToDefault("CharStyleName")` on the remove path only
- **Ghost advance**: flag+timer guard blocks deferred `modified()` events after re-insert
- **Right Arrow interception**: `XKeyHandler` never receives Right Arrow in LO Writer; dispatch interception is the only way
- **UNO module isolation**: components share state via `sys._llmac_handler`

## License

MIT
