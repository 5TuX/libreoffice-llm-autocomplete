# LLM Autocomplete for LibreOffice Writer

AI-powered writing suggestions for LibreOffice Writer, similar to GitHub Copilot. Type naturally and get inline completions from Claude, ChatGPT, or a free local model.

![LibreOffice Writer with ghost text suggestion](https://img.shields.io/badge/LibreOffice-7.x%2B-green) ![License: MIT](https://img.shields.io/badge/License-MIT-blue)

## Features

- **Inline suggestions** -- gray italic text appears at your cursor as you type
- **Accept all or word-by-word** -- grab the whole suggestion or scrub through it word by word
- **Undo word accept** -- went too far? Ctrl+Left puts words back into the suggestion
- **Type-through** -- keep typing and the suggestion shrinks to match; typed characters are automatically tagged as AI-generated
- **Highlight AI-generated text** -- toggle a green background on accepted/typed-through AI text to see exactly what came from the model
- **Context-aware** -- uses text before *and* after your cursor for smarter suggestions
- **Works with any provider** -- Claude, ChatGPT, Ollama (free & local), or any compatible API
- **Configurable sidebar** -- change settings without leaving Writer

## Keyboard Shortcuts

When a suggestion is visible (gray italic text):

| Key | Action |
|-----|--------|
| **→** (Right Arrow) | Accept entire suggestion |
| **Ctrl+→** | Accept next word |
| **Ctrl+←** | Un-accept last word (put it back into suggestion) |
| **Esc** | Dismiss suggestion |
| **Any character** | If it matches the suggestion start, the suggestion shrinks (type-through). Otherwise, dismisses and types normally. |
| **←  ↑  ↓  Home  End** | Dismiss suggestion and navigate normally |

You can hold **Ctrl** and scrub **→ / ←** to move the accept boundary back and forth through the suggestion words before committing.

## Install

1. Download `LLMAutocomplete.oxt` from the [Releases page](../../releases)
2. Double-click the file, or open LibreOffice > **Tools > Extension Manager > Add**
3. Restart LibreOffice

## Setup

1. In Writer, open **View > Sidebar**
2. Click the **LLM Autocomplete** panel
3. Expand **API Settings** and enter your API key
4. Click **Save Settings**
5. Start typing!

### Which provider should I use?

| Provider | Cost | Setup |
|----------|------|-------|
| **Anthropic Claude** (recommended) | ~$0.001/suggestion | Get a key at [console.anthropic.com](https://console.anthropic.com) |
| **OpenAI / ChatGPT** | Similar | Get a key at [platform.openai.com](https://platform.openai.com). Set Base URL to `https://api.openai.com/v1` |
| **Ollama** (free, private) | Free | Install [Ollama](https://ollama.com), set Base URL to `http://localhost:11434/v1`, leave API key blank |

### Settings

The sidebar lets you configure:

- **API Key** -- your provider's key (stored locally only)
- **Model** -- which model to use (default: `claude-haiku-4-5-20251001`, the fastest/cheapest)
- **Base URL** -- API endpoint (change this for OpenAI or Ollama)
- **Max tokens** -- suggestion length (default: 80)
- **Max context chars** -- how much surrounding text to send (default: 500)
- **Single sentence** -- limit suggestions to one sentence (on by default)
- **Highlight AI-generated text** -- toggle green background on AI text (off by default)

Advanced timer settings are available under the **Debugging** section for fine-tuning responsiveness.

### Privacy

- Your API key is stored **locally** in `~/.llmautocomplete/settings.json`
- Text around your cursor (up to 500 chars each side) is sent to your chosen API
- **No telemetry, no tracking, no third-party servers**
- For full privacy, use Ollama -- everything stays on your machine

## Requirements

- LibreOffice 7.x+ (Writer)
- An API key (or Ollama for free local use)

## Building from Source

```bash
git clone https://github.com/5TuX/libreoffice-llm-autocomplete.git
cd libreoffice-llm-autocomplete
bash build.sh
# Install the built extension:
unopkg add LLMAutocomplete.oxt
```

## Technical Details

<details>
<summary>Click to expand</summary>

### How suggestions work

1. You type text -- a debounce timer waits 600ms after your last keystroke
2. Text before and after your cursor is sent to the LLM API (continue prompt if cursor is at end, infill prompt if mid-document)
3. The suggestion comes back and is inserted as styled ghost text
4. You accept, dismiss, or keep typing through it

### Architecture

Ghost text is implemented as real characters with a custom `CharacterStyle` (gray italic). The extension uses three UNO mechanisms to handle input:

- `XModifyListener` -- detects document changes to trigger suggestions
- `XDispatchProviderInterceptor` -- intercepts Right Arrow and navigation commands
- `XKeyHandler` -- handles Escape, Ctrl+Right (accept word), and Ctrl+Left (un-accept word)

### Extension structure

```
LLMAutocomplete.oxt (ZIP)
├── META-INF/manifest.xml
├── description.xml
├── Jobs.xcu, Factory.xcu, Sidebar.xcu
├── images/icon.png
└── python/
    ├── LLMAutoComplete.py          # core logic
    ├── SidebarPanel.py             # sidebar UI
    └── pythonpath/
        ├── llm_client.py           # API client
        └── settings_store.py       # settings persistence
```

### Debug log

```bash
tail -f ~/llmautocomplete_debug.log
```

</details>

## License

MIT
