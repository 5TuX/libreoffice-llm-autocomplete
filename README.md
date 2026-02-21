# LLM Autocomplete for LibreOffice Writer

Real-time AI-powered ghost text autocomplete for LibreOffice Writer, similar to GitHub Copilot. Works with Anthropic Claude, OpenAI, or any compatible API including local models via Ollama.

![LibreOffice Writer with ghost text suggestion](https://img.shields.io/badge/LibreOffice-7.x%2B-green) ![License: MIT](https://img.shields.io/badge/License-MIT-blue)

## Features

- **Inline ghost text** -- gray italic suggestions appear at your cursor as you type
- **Word-by-word accept** -- Ctrl+Right accepts one word at a time (Copilot-style)
- **Type-through** -- keep typing and the suggestion shrinks as you match it
- **Bidirectional context** -- when cursor is mid-document, sends text before AND after cursor for smarter infill suggestions
- **Stale suggestion guard** -- discards suggestions if you moved the cursor while the API was responding
- **Multi-provider** -- Anthropic Claude, OpenAI, Ollama, or any OpenAI-compatible endpoint
- **Sidebar panel** -- configure API key, model, timers, and see live status
- **Privacy-first** -- no telemetry, no data collection; use Ollama for fully local inference

## Keyboard Shortcuts

| Action | Key |
|--------|-----|
| Accept entire suggestion | **Right Arrow** |
| Accept next word | **Ctrl+Right** |
| Dismiss suggestion | **Escape** |
| Type through | Type matching characters (suggestion shrinks) |

Navigation keys (Left Arrow, Up, Down, Home, End, Ctrl+Left) automatically dismiss the current suggestion.

## Install

### From Release (easiest)

1. Download `LLMAutocomplete.oxt` from [Releases](../../releases)
2. Double-click the `.oxt` file, or open LibreOffice > **Tools > Extension Manager > Add**
3. Restart LibreOffice

### From Source

```bash
git clone https://github.com/5TuX/libreoffice-llm-autocomplete.git
cd libreoffice-llm-autocomplete
bash build.sh
# Then install LLMAutocomplete.oxt via Extension Manager or:
# unopkg add LLMAutocomplete.oxt
```

## Setup

1. Open Writer, go to **View > Sidebar**
2. Click the **LLM Autocomplete** panel
3. Expand **API Settings**, enter your API key and model name
4. Click **Save Settings**
5. Start typing -- suggestions appear after a brief pause

### Supported Providers

| Provider | Base URL | Model example | API key |
|----------|----------|---------------|---------|
| **Anthropic Claude** (recommended) | `https://api.anthropic.com/v1` | `claude-haiku-4-5-20251001` | [console.anthropic.com](https://console.anthropic.com) |
| **OpenAI** | `https://api.openai.com/v1` | `gpt-4o-mini` | [platform.openai.com](https://platform.openai.com) |
| **Ollama** (free, local) | `http://localhost:11434/v1` | `llama3` | Leave blank |
| **Any OpenAI-compatible** | Your endpoint URL | Your model name | Your key |

### Sidebar Settings

| Setting | Default | What it does |
|---------|---------|--------------|
| API Key | -- | Your provider's API key |
| Model | `claude-haiku-4-5-20251001` | Model name to use |
| Base URL | `https://api.anthropic.com/v1` | API endpoint |
| Max tokens | 80 | Maximum tokens per suggestion |
| Max context chars | 500 | How many characters before/after cursor to send |
| Single sentence | On | Truncate suggestions after first sentence |

### Timer Settings (Debugging section)

| Setting | Default | What it does |
|---------|---------|--------------|
| Debounce | 600 ms | Wait after last keystroke before querying the API |
| Advance timer | 20 ms | Time window to suppress deferred events during type-through |
| Poll drain | 300 ms | Interval for checking the suggestion queue |
| Poll drain init | 1000 ms | Initial delay before first poll |
| Status poll | 500 ms | Interval for updating the sidebar status label |
| Status poll init | 2000 ms | Initial delay before first status poll |

### Privacy

- API key stored **locally only** in `~/.llmautocomplete/settings.json`
- Text context (up to 500 chars before and after cursor) is sent to your configured API endpoint
- **No telemetry, no data collection, no third-party servers**
- For maximum privacy, use Ollama (everything stays on your machine)

## Requirements

- LibreOffice 7.x+ (Writer)
- Windows, macOS, or Linux
- Internet access (or local LLM via Ollama)
- API key from Anthropic, OpenAI, or compatible provider

## How It Works (Technical)

### Ghost text

Suggestions are inserted as real characters styled with a custom `CharacterStyle` ("LLMSuggestion" -- gray italic). The view cursor stays before the ghost text so the user can keep typing normally. When accepted, the style is reset to match surrounding text; when dismissed, the characters are deleted.

### Suggestion lifecycle

```
User types → modified() fires → debounce timer starts (600ms)
  → timer fires → _fire_request() on background thread
  → get prefix (text before cursor) + suffix (text after cursor)
  → call LLM API with appropriate prompt (continue vs infill)
  → push suggestion to queue
  → drain_queue() on main thread checks staleness, inserts ghost
```

### Input interception

| Mechanism | Used for | Why |
|-----------|----------|-----|
| `XModifyListener` | Detect typing, trigger suggestions | Reliable for all text changes |
| `XDispatchProviderInterceptor` | Right Arrow accept, navigation dismiss | Only way to intercept `.uno:GoRight` on Windows LO |
| `XKeyHandler` | Escape dismiss, Ctrl+Right word accept | Escape works here; Ctrl+Right dispatch never fires on Windows |

### Key technical challenges solved

- **Style leak prevention**: `setPropertyToDefault("CharStyleName")` on the remove path only -- preserves user formatting (bold, color)
- **Ghost advance (type-through)**: flag+timer guard (20ms) blocks deferred `modified()` events after re-inserting shortened ghost
- **Right Arrow interception**: `XKeyHandler` never receives Right Arrow in LO Writer on Windows; `XDispatchProviderInterceptor` for `.uno:GoRight` is the only way
- **Ctrl+Right interception**: `.uno:GoWordRight` dispatch never fires on Windows LO; handled via `keyPressed` (reports as keyCode=1027 with Ctrl modifier)
- **UNO module isolation**: Python UNO components load in isolated namespaces; shared state via `sys._llmac_handler`
- **Stale suggestion guard**: saves context prefix at API request time; at insertion time, verifies current prefix still starts with saved prefix (allows forward typing, rejects cursor jumps)
- **Bidirectional context**: sends text after cursor as suffix; uses infill system prompt when suffix exists, continue prompt when cursor is at end

### Extension structure

```
LLMAutocomplete.oxt (ZIP)
├── META-INF/manifest.xml        # declares Python components + config files
├── description.xml              # extension metadata
├── Jobs.xcu                     # startup job registration
├── Factory.xcu                  # sidebar panel factory
├── Sidebar.xcu                  # sidebar deck/panel declaration
├── empty_dialog.xdl             # empty dialog for panel container
├── images/icon.png              # sidebar icon
└── python/
    ├── LLMAutoComplete.py       # core: handler, ghost text, dispatch interceptors
    ├── SidebarPanel.py          # sidebar UI: settings, status, controls
    └── pythonpath/
        ├── llm_client.py        # LLM API client (Anthropic + OpenAI-compatible)
        └── settings_store.py    # JSON settings persistence
```

## Development

```bash
# Build extension
bash build.sh

# Install (removes old version first)
unopkg remove com.example.llmautocomplete 2>/dev/null
unopkg add LLMAutocomplete.oxt

# Launch Writer
soffice --writer

# Debug log
tail -f ~/llmautocomplete_debug.log
```

## License

MIT
