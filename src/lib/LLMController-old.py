"""
app.py — WSD Dinosaur List  (NLP / TF-IDF RAG edition)

How it works
────────────
0. Preprocess (NLPPreprocessor — NLTK):
             Every text (README or query) passes through a 4-step pipeline
             before vectorisation:
               a) Regex tokenisation   — extract alphanumeric tokens
               b) Stop-word removal    — NLTK English stopwords list
               c) Lemmatisation        — WordNetLemmatizer with POS context
                                         ("running" → "run", "better" → "good")
               d) Stemming             — PorterStemmer for root normalisation
                                         ("studies" → "studi", "jumping" → "jump")
             Applying the same pipeline to both index documents and live
             queries ensures vocabulary alignment at retrieval time.

1. Index   : Every preprocessed README is converted into a TF-IDF vector
             using a vocabulary built from all documents.
             TF-IDF (Term Frequency – Inverse Document Frequency):
             common words get low weight, rare/distinctive words get high
             weight, so each tool's unique terminology stands out.

2. Retrieve: The user's query is preprocessed then vectorised with the same
             TF-IDF vocabulary.  Cosine similarity ranks every tool README
             against the query.  The closest match (above a threshold) is
             selected.

3. Generate: Only the matched tool's README is sent to the LLM as context,
             keeping the prompt short and focused (RAG pattern).

Dependencies: numpy (vectors), nltk (NLP preprocessing), llama-cpp-python (LLM).
"""

import re
import math
import time
import numpy as np
import nltk
from pathlib import Path
from collections import Counter
from nltk.corpus import stopwords, wordnet
from nltk.stem import WordNetLemmatizer, PorterStemmer
from nltk.tag import pos_tag
from llama_cpp import Llama


# ── NLTK data bootstrap ───────────────────────────────────────────────────────
def _ensure_nltk_data() -> None:
    """Download required NLTK corpora on first run (silent if already present)."""
    _packages = [
        ("stopwords",                      "corpora/stopwords"),
        ("wordnet",                        "corpora/wordnet"),
        ("averaged_perceptron_tagger_eng", "taggers/averaged_perceptron_tagger_eng"),
        ("punkt_tab",                      "tokenizers/punkt_tab"),
    ]
    for pkg, path in _packages:
        try:
            nltk.data.find(path)
        except LookupError:
            nltk.download(pkg, quiet=True)


_ensure_nltk_data()


# ── NLP Preprocessor ──────────────────────────────────────────────────────────
class NLPPreprocessor:
    """
    4-step NLP preprocessing pipeline using NLTK.

    Pipeline (applied in order):
        1. Regex tokenisation  — extract alphanumeric tokens, lowercase
        2. Stop-word removal   — NLTK English stopwords list
        3. Lemmatisation       — WordNetLemmatizer with POS context
                                 e.g. "running" → "run", "better" → "good"
        4. Stemming            — PorterStemmer for aggressive root normalisation
                                 e.g. "studies" → "studi", "jumping" → "jump"

    Applying the same pipeline to both index documents and live queries
    ensures the query vocabulary aligns perfectly with the indexed vocabulary.
    """

    _WORDNET_POS_MAP: dict = {
        "J": wordnet.ADJ,
        "V": wordnet.VERB,
        "N": wordnet.NOUN,
        "R": wordnet.ADV,
    }

    def __init__(self, use_lemmatize: bool = True, use_stemming: bool = True):
        self.use_lemmatize = use_lemmatize
        self.use_stemming  = use_stemming
        self._lemmatizer   = WordNetLemmatizer()
        self._stemmer      = PorterStemmer()
        self._stop_words   = set(stopwords.words("english"))

    def _wordnet_pos(self, treebank_tag: str) -> str:
        """Map a Penn Treebank POS tag to the closest WordNet POS constant."""
        return self._WORDNET_POS_MAP.get(treebank_tag[0] if treebank_tag else "N",
                                         wordnet.NOUN)

    def preprocess(self, text: str) -> list[str]:
        """Return a token list after applying the full NLP pipeline."""
        # 1. Tokenise + lowercase
        tokens: list[str] = re.findall(r"[a-zA-Z][a-zA-Z0-9]*", text.lower())

        # 2. Stop-word removal + minimum length guard
        tokens = [t for t in tokens if t not in self._stop_words and len(t) > 1]

        # 3. Lemmatisation with POS-aware context
        if self.use_lemmatize and tokens:
            tagged = pos_tag(tokens)
            tokens = [
                self._lemmatizer.lemmatize(word, self._wordnet_pos(tag))
                for word, tag in tagged
            ]

        # 4. Stemming
        if self.use_stemming:
            tokens = [self._stemmer.stem(t) for t in tokens]

        return tokens


# ── TF-IDF Vectoriser ─────────────────────────────────────────────────────────
class TFIDFVectoriser:
    """
    Lightweight TF-IDF vectoriser built on numpy + NLTK preprocessing.

    Workflow:
        fit(corpus)           → preprocess + build vocabulary + IDF weights
        transform(texts)      → preprocess + convert texts to TF-IDF matrix (n × vocab)
        cosine_similarity(a,b)→ float in [0, 1]

    Text preprocessing is delegated to NLPPreprocessor (stop-word removal,
    lemmatisation, stemming) so both index documents and live queries share
    an identical token space.
    """

    def __init__(self, preprocessor: "NLPPreprocessor | None" = None):
        self.vocab:         dict[str, int]   = {}   # term → column index
        self.idf:           np.ndarray       = np.array([])
        self._n_docs:       int              = 0
        self._preprocessor: NLPPreprocessor  = preprocessor or NLPPreprocessor()

    # ── helpers ───────────────────────────────────────────────────────────────
    def _tokenise(self, text: str) -> list[str]:
        """Delegate tokenisation to the NLP preprocessing pipeline."""
        return self._preprocessor.preprocess(text)

    def _tf(self, tokens: list[str]) -> dict[str, float]:
        """Term-frequency: count(term) / total_terms."""
        counts = Counter(tokens)
        total  = max(len(tokens), 1)
        return {t: c / total for t, c in counts.items()}

    # ── public API ────────────────────────────────────────────────────────────
    def fit(self, corpus: list[str]) -> "TFIDFVectoriser":
        """Build vocabulary and IDF weights from a list of document strings."""
        self._n_docs = len(corpus)
        doc_freq: dict[str, int] = {}

        for text in corpus:
            unique_terms = set(self._tokenise(text))
            for term in unique_terms:
                doc_freq[term] = doc_freq.get(term, 0) + 1

        # Assign column indices and compute IDF with smoothing
        self.vocab = {term: idx for idx, term in enumerate(sorted(doc_freq))}
        self.idf   = np.array([
            math.log((self._n_docs + 1) / (doc_freq[t] + 1)) + 1.0
            for t in sorted(doc_freq)
        ])
        return self

    def transform(self, texts: list[str]) -> np.ndarray:
        """Return a TF-IDF matrix of shape (len(texts), len(vocab))."""
        vocab_size = len(self.vocab)
        matrix     = np.zeros((len(texts), vocab_size), dtype=np.float32)
        for row, text in enumerate(texts):
            tf = self._tf(self._tokenise(text))
            for term, freq in tf.items():
                col = self.vocab.get(term)
                if col is not None:
                    matrix[row, col] = freq * self.idf[col]
        # L2-normalise each row so cosine similarity = dot product
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return matrix / norms

    def transform_query(self, query: str) -> np.ndarray:
        """Return a single normalised TF-IDF row vector for a query string."""
        return self.transform([query])[0]

    @staticmethod
    def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        """Cosine similarity between two L2-normalised vectors."""
        return float(np.dot(a, b))


# ── Main chatbot class ────────────────────────────────────────────────────────
class DinosaurVectorBot:
    """NLP-powered RAG chatbot for WSD Dinosaur List (TF-IDF edition)."""

    # ── ANSI colours ──────────────────────────────────────────────────────────
    BOLD_CYAN   = "\033[1;36m"
    BOLD_GREEN  = "\033[1;32m"
    BOLD_YELLOW = "\033[1;33m"
    RESET       = "\033[0m"

    NOT_FOUND_MARKER     = "[NOT_FOUND]"
    CHAR_DELAY           = 0.012   # seconds per character (typewriter effect)
    SIMILARITY_THRESHOLD = 0.20    # minimum cosine similarity to count as a match
    MIN_QUERY_TOKENS     = 2       # preprocessed tokens required before attempting retrieval

    # Words that indicate greetings or small talk (no tool search needed)
    SMALL_TALK_WORDS = {
        "hi", "hello", "hey", "howdy", "hiya", "greetings",
        "morning", "afternoon", "evening", "night",
        "thanks", "thank", "thx", "cheers",
        "ok", "okay", "cool", "great", "nice", "good", "fine", "sure",
        "how", "are", "you", "doing", "well", "wassup", "sup",
        "welcome", "bye", "cya", "later",
    }

    # Words that signal the user wants a list of tools, not a single recommendation
    LIST_TRIGGER_WORDS = {
        "list", "show", "display", "all", "available",
        "what", "which", "any", "related", "tools", "tool",
    }

    # Words that signal the user wants a full explanation, not a brief intro
    ELABORATE_WORDS = {
        "explain", "elaborate", "describe",
        "detail", "details", "overview",
        "feature", "features", "more", "about", "how",
    }

    # ── System prompts ────────────────────────────────────────────────────────
    CHAT_SYSTEM_PROMPT = """You are a warm and friendly assistant for WSD Dinosaur List.
The user is greeting you or making small talk — respond naturally and conversationally.
Be polite and welcoming. Let them know you are here to help them find the right software tool.
Keep your reply to 2 sentences maximum. Do NOT recommend tools unless the user asks."""

    # Used ONLY for describe/elaborate requests.
    # Brief replies are built from a template — no LLM involved.
    SYSTEM_PROMPT = """Summarize the following README for a user of WSD Dinosaur List.
Rules:
1. Start with: **ToolName**, developed by Author. (Author is under "Developer:" in the text.)
2. Write 3 to 5 sentences about what the tool does and its main features.
3. Use ONLY words and technical terms that appear in the README. Never add or invent details.
4. Stop after 5 sentences. Do not repeat yourself."""

    CODE_SYSTEM_PROMPT = """You are a friendly coding assistant for WSD Dinosaur List.
Write a practical code suggestion to help the user achieve a goal not covered by available tools.
Rules: be warm, brief explanation first, then clean working code, offer to refine."""

    # ── Constructor ───────────────────────────────────────────────────────────
    def __init__(
        self,
        model_path: str = None,
        source_dir: str = None,
        n_ctx:      int = 2048,
    ):
        # Resolve paths relative to project root (two levels up from src/lib/)
        _root = Path(__file__).parent.parent.parent
        if model_path is None:
            model_path = str(_root / "models" / "qwen2.5-0.5b-instruct-q4_k_m.gguf")
        if source_dir is None:
            source_dir = str(_root / "App_Store")

        self.source_dir  = source_dir
        self.chunks:     list[str]  = []   # full README text per tool
        self.meta:       list[dict] = []   # {name, author} per tool
        self.tfidf_matrix: np.ndarray | None = None
        self._last_tool: dict | None = None   # context memory for "describe" follow-ups

        print("🔤 Initialising NLP preprocessor (stop-words → lemmatise → stem)...")
        self._preprocessor = NLPPreprocessor(use_lemmatize=True, use_stemming=True)
        self.vectoriser    = TFIDFVectoriser(preprocessor=self._preprocessor)

        print("🧠 Loading LLM...")
        self.llm = Llama(model_path=model_path, n_ctx=n_ctx, verbose=False)

    # ── Static helpers ────────────────────────────────────────────────────────
    @staticmethod
    def _extract_meta(text: str) -> dict:
        """Pull app name and author from README — supports both formats:
          New flat: TOOL: <name>  /  AUTHOR: <name>
          Old MD:   # <name>      /  **Author:** <name>
        """
        name = author = ""
        for line in text.splitlines():
            s = line.strip()
            # New flat format
            if s.upper().startswith("TOOL:") and not name:
                name = s.split(":", 1)[-1].strip()
            if s.upper().startswith("AUTHOR:") and not author:
                author = s.split(":", 1)[-1].strip()
            # Old markdown format (fallback)
            if s.startswith("# ") and not name:
                name = s[2:].strip()
            if "**Author:**" in s and not author:
                author = s.split("**Author:**")[-1].strip(" -")
        return {"name": name or "Unknown", "author": author or "Unknown"}

    @staticmethod
    def _extract_description(text: str) -> str:
        """Return the purpose line — supports both formats:
          New flat: PURPOSE: <description>
          Old MD:   first sentence of ## Overview
        """
        in_overview = False
        for line in text.splitlines():
            s = line.strip()
            # New flat format
            if s.upper().startswith("PURPOSE:"):
                return s.split(":", 1)[-1].strip()
            # Old markdown format (fallback)
            if s == "## Overview":
                in_overview = True
                continue
            if in_overview and s and not s.startswith("#") and not s.startswith("-"):
                return s.split(".")[0] + "."
        return "A tool available in the WSD Dinosaur List."

    @staticmethod
    def _fmt(text: str) -> str:
        """Replace **text** with bold cyan ANSI."""
        return re.sub(
            r"\*\*(.+?)\*\*",
            lambda m: f"{DinosaurVectorBot.BOLD_CYAN}{m.group(1)}{DinosaurVectorBot.RESET}",
            text,
        )

    # ── Indexing ──────────────────────────────────────────────────────────────
    def load_and_index(self) -> bool:
        """Read all README files, build TF-IDF index. Returns True on success."""
        source_path  = Path(self.source_dir)
        readme_files = list(source_path.rglob("*README*"))

        if not readme_files:
            print("❌ No README files found in Source folder.")
            return False

        for path in readme_files:
            content = path.read_text(encoding="utf-8", errors="ignore")
            meta    = self._extract_meta(content)
            self.chunks.append(content)
            self.meta.append(meta)
            print(f"  ✓ Indexed: {meta['name']}  ({path.name})")

        print(f"\n  🔢 Building TF-IDF vectors for {len(self.chunks)} tool(s)...")
        self.vectoriser.fit(self.chunks)
        self.tfidf_matrix = self.vectoriser.transform(self.chunks)
        return True

    def reload_and_index(self) -> bool:
        """Clear the existing index and re-scan App_Store READMEs.

        Call this after a Box refresh adds or updates README files so the
        AI assistant picks up the latest content without restarting.
        """
        self.chunks.clear()
        self.meta.clear()
        self.tfidf_matrix = None
        self._last_tool   = None
        return self.load_and_index()

    def welcome_message(self) -> str:
        """Return a startup keyword guide derived from the indexed READMEs.

        Lists up to 3 keywords per tool so users know what vocabulary to use.
        Safe to call only after load_and_index() has completed.
        """
        lines = [
            "Here are some keywords you can use to find a tool:",
            "",
        ]
        for chunk, m in zip(self.chunks, self.meta):
            for line in chunk.splitlines():
                if line.strip().upper().startswith("KEYWORDS:"):
                    kws_raw = line.split(":", 1)[-1].strip().split(",")
                    kws     = [k.strip() for k in kws_raw[:3] if k.strip()]
                    lines.append(f"  {m['name']}: {', '.join(kws)}")
                    break
        lines += [
            "",
            "Type 'list all tools' to browse everything,",
            "or describe what you need and I will find the best match.",
        ]
        return "\n".join(lines)

    # ── Retrieval ─────────────────────────────────────────────────────────────
    def retrieve(self, query: str, top_k: int = 1) -> list[dict]:
        """Return top_k matching tools above the similarity threshold.

        Fast path: if any tool name appears verbatim in the raw query (e.g.
        'what can QuickMi2e do?'), return that tool immediately with score 1.0
        — no TF-IDF needed and threshold is bypassed.

        Normal path: TF-IDF cosine similarity ranked by score.
        """
        # Fast path — exact tool-name substring match in raw query
        q_lower = query.lower()
        for i, meta in enumerate(self.meta):
            if meta["name"].lower() in q_lower:
                return [{
                    "score":   1.0,
                    "content": self.chunks[i],
                    "name":    meta["name"],
                    "author":  meta["author"],
                }]

        # Normal path — TF-IDF cosine similarity
        q_vec  = self.vectoriser.transform_query(query)
        scores = [
            TFIDFVectoriser.cosine_similarity(q_vec, self.tfidf_matrix[i])
            for i in range(len(self.chunks))
        ]
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]

        return [
            {
                "score":   round(scores[i], 3),
                "content": self.chunks[i],
                "name":    self.meta[i]["name"],
                "author":  self.meta[i]["author"],
            }
            for i in ranked
            if scores[i] >= self.SIMILARITY_THRESHOLD
        ]

    # ── Template responses (no LLM) ──────────────────────────────────────────
    def _build_brief_response(self, best: dict) -> str:
        """One-liner intro pulled verbatim from the README — no LLM."""
        desc = self._extract_description(best["content"])
        return (
            f"{best['name']}, developed by {best['author']}.\n"
            f"{desc}\n\n"
            "Type 'describe' for more details."
        )

    def _build_describe_response(self, best: dict) -> str:
        """Full description assembled from README sections — no LLM, no hallucination.

        Handles both the new flat-header format (TOOL:, FORMATS:, KEYWORDS: …)
        and the legacy markdown format (- **Key:** Value) as a fallback.
        Extracts: header fields → Overview → Formats → Features → Use Cases.
        """
        lines      = best["content"].splitlines()
        overview   : list[str] = []
        features   : list[str] = []
        use_cases  : list[str] = []
        formats    : str = ""
        in_section : str | None = None

        _SKIP_FLAT = {"tool", "author", "platform", "keywords"}   # shown elsewhere

        for line in lines:
            s = line.strip()
            if not s:
                continue

            # ── Flat header key: value (new format) ───────────────────────
            if in_section is None and ":" in s \
                    and not s.startswith("#") and not s.startswith("-"):
                key, _, val = s.partition(":")
                key_clean = key.strip().lower()
                val_clean = val.strip()
                if key_clean == "formats" and val_clean \
                        and val_clean.upper() != "N/A":
                    formats = val_clean
                continue

            # ── Section transitions ────────────────────────────────────────
            sl = s.lower()
            if sl in ("## overview", "## objective"):
                in_section = "overview"; continue
            if sl in ("## feature list", "## features"):
                in_section = "features"; continue
            if sl == "## use cases":
                in_section = "usecases"; continue
            if s.startswith("## ") or s == "---":
                in_section = None; continue

            # ── Legacy markdown metadata bullets (old format fallback) ─────
            if in_section is None and s.startswith("- **") and ":**" in s:
                key_md = s.split(":**")[0].lstrip("- **").lower()
                val_md = s.split(":**", 1)[-1].strip()
                if "format" in key_md and val_md:
                    formats = val_md
                continue

            # ── Section content ────────────────────────────────────────────
            if in_section == "overview":
                overview.append(s)
            elif in_section == "features" and s.startswith("- "):
                features.append(s[2:])
            elif in_section == "usecases" and s.startswith("- "):
                use_cases.append(s[2:])

        parts: list[str] = [f"{best['name']}, developed by {best['author']}.\n"]

        if overview:
            parts.append(overview[0])
            parts.append("")

        if formats:
            parts.append(f"• Supported Formats: {formats}")
            parts.append("")

        if features:
            parts.append("[ Features ]")
            for feat in features[:8]:
                parts.append(f"• {feat}")

        if use_cases:
            parts.append("")
            parts.append("[ Use Cases ]")
            for uc in use_cases[:4]:
                parts.append(f"• {uc}")

        return "\n".join(parts)

    # ── Prompt builders ───────────────────────────────────────────────────────
    def _build_rag_prompt(self, context: str, query: str, author: str = "") -> str:
        developer_line = f"Developer: {author}\n\n" if author else ""
        return (
            "Context — README for the best-matching tool in WSD Dinosaur List:\n\n"
            f"{developer_line}{context}\n\n---\n\nUser question: {query}"
        )

    def _build_code_prompt(self, query: str) -> str:
        return (
            f'The user needs help with: "{query}"\n\n'
            "No matching tool was found in the WSD Dinosaur List. "
            "Please write a practical code suggestion to help the user achieve their goal."
        )

    # ── Typewriter streaming ──────────────────────────────────────────────────
    def _emit(self, text: str) -> None:
        for ch in text:
            if ch == "\n":
                print(flush=True)
            else:
                print(ch, end="", flush=True)
                time.sleep(self.CHAR_DELAY)

    def _flush_buf(self, buf: str, in_bold: list) -> str:
        display = buf.replace(self.NOT_FOUND_MARKER, "")
        while "**" in display:
            idx = display.index("**")
            if display[:idx]:
                self._emit(display[:idx])
            display = display[idx + 2:]
            if in_bold[0]:
                print(self.RESET, end="", flush=True)
                in_bold[0] = False
            else:
                print(self.BOLD_CYAN, end="", flush=True)
                in_bold[0] = True
        self._emit(display)
        return ""

    def _stream_and_collect(self, messages: list, max_tokens: int = 280) -> tuple:
        """Stream LLM output with typewriter + bold effects.
        Returns (full_text, not_found_flag)."""
        stream = self.llm.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.1,
            repeat_penalty=1.5,        # stronger penalty to stop repetition loops
            presence_penalty=0.6,      # penalise reusing any token already seen
            stream=True,
        )

        buf = full_response = ""
        in_bold = [False]
        marker  = self.NOT_FOUND_MARKER

        for chunk in stream:
            token = chunk["choices"][0]["delta"].get("content", "")
            if not token:
                continue
            full_response += token
            buf           += token

            # Hold if buf tail could be a partial special marker
            partial = any(buf.endswith(marker[:i]) for i in range(1, len(marker)))
            if buf.endswith("*") or partial:
                continue
            buf = self._flush_buf(buf, in_bold)

        if buf:
            self._flush_buf(buf, in_bold)
        if in_bold[0]:
            print(self.RESET, end="", flush=True)

        print("\n")
        return full_response, self.NOT_FOUND_MARKER in full_response

    # ── Intent detection ──────────────────────────────────────────────────────
    def _is_small_talk(self, query: str) -> bool:
        """Return True if the query is a greeting or small talk with no tool intent.

        Queries that contain enough meaningful NLP tokens (tool names, technical
        keywords) are never classified as small talk — this prevents false positives
        like "can you explain QuickMi2e" being caught by the 'you' stop-word.
        """
        if self._has_meaningful_content(query):
            return False
        words = set(re.findall(r"[a-z]+", query.lower()))
        if len(words) <= 6 and words & self.SMALL_TALK_WORDS:
            return True
        if words <= self.SMALL_TALK_WORDS:
            return True
        return False

    def _is_list_request(self, query: str) -> bool:
        """Return True if the user wants to list / browse multiple tools."""
        words = set(re.findall(r"[a-z]+", query.lower()))
        has_list_word = bool(words & {"list", "show", "display", "all", "available",
                                      "what", "which", "any", "related", "have", "got"})
        has_tool_word = bool(words & {"tool", "tools"})
        # Also catch "what do you have" / "show me everything"
        has_browse = bool(words & {"everything", "anything", "catalog", "catalogue"})
        return (has_list_word and has_tool_word) or has_browse

    def _is_elaborate_request(self, query: str) -> bool:
        """Return True if the user wants a detailed explanation rather than a brief intro."""
        words = set(re.findall(r"[a-z]+", query.lower()))
        return bool(words & self.ELABORATE_WORDS)

    def _has_meaningful_content(self, query: str) -> bool:
        """Return True only if the query has enough meaningful tokens after NLP
        preprocessing.  Queries that reduce to fewer than MIN_QUERY_TOKENS
        (e.g. pure stop-words, generic phrases like 'can you help me') are
        rejected before any similarity search is attempted."""
        return len(self._preprocessor.preprocess(query)) >= self.MIN_QUERY_TOKENS

    def _has_tool_name_match(self, query: str) -> bool:
        """Return True if any indexed tool name appears verbatim in the raw query.

        This bypasses MIN_QUERY_TOKENS for queries like 'what can QuickMi2e do?'
        where all surrounding words are stop-words that get stripped, leaving only
        the tool name as a single token — not enough for the normal content gate.
        """
        q = query.lower()
        return any(m["name"].lower() in q for m in self.meta)

    # ── High-level responses ──────────────────────────────────────────────────
    def respond_chat(self, user_query: str) -> None:
        """Handle greetings and small talk conversationally — no tool search."""
        print(f"{self.BOLD_CYAN}💬 {self.RESET}\n")
        self._stream_and_collect([
            {"role": "system", "content": self.CHAT_SYSTEM_PROMPT},
            {"role": "user",   "content": user_query},
        ])

    def respond_list(self, query: str) -> bool:
        """List tools from the Dinosaur List.
        - If a keyword is present: shows all tools sorted by relevance score.
        - If generic ("list all tools"): shows every tool regardless of score.
        Returns False always (listing is never a not-found situation)."""

        # Try a keyword-filtered retrieval first (no threshold — keep all scores)
        q_vec  = self.vectoriser.transform_query(query)
        scores = [
            TFIDFVectoriser.cosine_similarity(q_vec, self.tfidf_matrix[i])
            for i in range(len(self.chunks))
        ]
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)

        # If the top score is meaningful, show only those above threshold (keyword search)
        if scores[ranked[0]] >= self.SIMILARITY_THRESHOLD:
            results = [
                {
                    "score":   round(scores[i], 3),
                    "content": self.chunks[i],
                    "name":    self.meta[i]["name"],
                    "author":  self.meta[i]["author"],
                }
                for i in ranked
                if scores[i] >= self.SIMILARITY_THRESHOLD
            ]
            header = "Here are the matching tools from the Dinosaur List:"
        else:
            # No keyword signal — user just wants to see everything
            results = [
                {
                    "score":   round(scores[i], 3),
                    "content": self.chunks[i],
                    "name":    self.meta[i]["name"],
                    "author":  self.meta[i]["author"],
                }
                for i in ranked
            ]
            header = "Here are all the tools we currently have in the Dinosaur List:"

        print(f"{self.BOLD_CYAN}💬 {header}{self.RESET}\n")
        for idx, r in enumerate(results, 1):
            desc      = self._extract_description(r["content"])
            name_bold = f"{self.BOLD_CYAN}{r['name']}{self.RESET}"
            print(f"  {idx}. {name_bold}")
            print(f"     {desc}")
            print(f"     Built by {r['author']}")
            print()
        print("Feel free to ask me more about any of these tools! 😊\n")
        return False

    def _sorry_not_in_list(self, reason: str = "no_match") -> None:
        """Neutral no-match response — never lists or recommends tools.

        reason:
            'no_keyword' — query had no meaningful tokens after preprocessing.
            'no_match'   — tokens were present but no tool scored above threshold.
        """
        print(f"{self.BOLD_YELLOW}💬 {self.RESET}\n")
        if reason == "no_keyword":
            msg = (
                "I couldn't detect a specific keyword in your message.\n"
                "Please describe the task or problem you need help with — "
                "for example: 'band validation', 'data mapping', or 'GRR analysis'."
            )
        else:
            msg = (
                "I couldn't find a tool in the Dinosaur List that matches your request.\n"
                "Please try describing your need more specifically.\n"
                "You can also type 'list all tools' to browse what's available."
            )
        self._emit(msg)
        print("\n")

    def respond(self, user_query: str) -> bool:
        """Retrieve best match, generate and stream a reply. Returns not_found flag.

        Gate 1 — keyword content: query must yield >= MIN_QUERY_TOKENS after NLP.
        Gate 2 — similarity:      best score must be >= SIMILARITY_THRESHOLD.
        """
        # ── Context shortcut: bare "describe / explain" with no new tool specified ─
        if self._is_elaborate_request(user_query) \
                and not self._has_meaningful_content(user_query) \
                and not self._has_tool_name_match(user_query) \
                and self._last_tool is not None:
            print(f"{self.BOLD_CYAN}💬 More details on {self._last_tool['name']}:{self.RESET}\n")
            self._emit(self._build_describe_response(self._last_tool))
            print("\n")
            return False

        # Gate 1: reject content-free queries — unless a known tool name is present
        if not self._has_meaningful_content(user_query) \
                and not self._has_tool_name_match(user_query):
            self._sorry_not_in_list("no_keyword")
            return False

        results = self.retrieve(user_query, top_k=5)

        # Gate 2: no tool scored above threshold — do not recommend anything
        if not results:
            self._sorry_not_in_list("no_match")
            return True   # triggers code-suggestion offer

        self._last_tool = results[0]

        # ── Multiple matches: show them all as a list ──────────────────────────
        if len(results) > 1:
            print(f"{self.BOLD_CYAN}💬 Found {len(results)} matching tools:{self.RESET}\n")
            lines = []
            for n, r in enumerate(results, 1):
                desc = self._extract_description(r["content"])
                lines.append(f"{n}. {r['name']}  —  by {r['author']}")
                lines.append(f"   {desc}")
            lines.append("\nType a tool name to get details, or 'describe' for more on the top result.")
            self._emit("\n".join(lines))
            print("\n")
            return False

        # ── Single match ──────────────────────────────────────────────────────
        best = results[0]
        print(f"{self.BOLD_CYAN}💬 Here's what I found for you:{self.RESET}\n")

        if self._is_elaborate_request(user_query):
            self._emit(self._build_describe_response(best))
        else:
            self._emit(self._build_brief_response(best))
        print("\n")
        return False

    def respond_with_code(self, user_query: str) -> None:
        """Generate and stream a code suggestion for unmatched queries."""
        print(f"{self.BOLD_GREEN}💻 Here's a code suggestion for you:{self.RESET}\n")
        self._stream_and_collect(
            messages=[
                {"role": "system", "content": self.CODE_SYSTEM_PROMPT},
                {"role": "user",   "content": self._build_code_prompt(user_query)},
            ],
            max_tokens=700,
        )

    # ── GUI-safe streaming (no ANSI, no print, callback-based) ───────────────
    def _stream_gui(
        self,
        messages:   list,
        max_tokens: int = 280,
        on_token=None,
    ) -> tuple:
        """Stream LLM response without any terminal output.

        Calls ``on_token(text)`` for each displayable fragment so the GUI
        can append tokens to a widget in real time.
        Returns (full_response_text, not_found_flag).
        """
        stream = self.llm.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.1,
            repeat_penalty=1.5,
            presence_penalty=0.6,
            stream=True,
        )

        marker        = self.NOT_FOUND_MARKER
        full_response = ""
        buf           = ""

        for chunk in stream:
            token = chunk["choices"][0]["delta"].get("content", "")
            if not token:
                continue
            full_response += token
            buf           += token

            # Hold if buf could be a partial NOT_FOUND_MARKER prefix
            if any(buf.endswith(marker[:i]) for i in range(1, len(marker))):
                continue

            display = buf.replace(marker, "")
            if display and on_token:
                on_token(display)
            buf = ""

        if buf:
            display = buf.replace(marker, "")
            if display and on_token:
                on_token(display)

        return full_response, marker in full_response

    def query(self, user_query: str, on_token=None) -> tuple:
        """GUI entry point — full intent detection + TF-IDF retrieval + LLM generation.

        Handles small-talk, list requests, tool recommendations, and not-found
        responses.  All output goes through ``on_token`` instead of print.
        Returns (full_response_text, not_found_flag).
        """
        # ── Context shortcut: bare "describe / explain" with no new tool specified ─
        if self._is_elaborate_request(user_query) \
                and not self._has_meaningful_content(user_query) \
                and not self._has_tool_name_match(user_query) \
                and self._last_tool is not None:
            msg = self._build_describe_response(self._last_tool)
            if on_token:
                on_token(msg)
            return msg, False

        # ── Small talk / greeting ─────────────────────────────────────────────
        if self._is_small_talk(user_query):
            return self._stream_gui(
                messages=[
                    {"role": "system", "content": self.CHAT_SYSTEM_PROMPT},
                    {"role": "user",   "content": user_query},
                ],
                on_token=on_token,
            )

        # ── List / browse request ─────────────────────────────────────────────
        if self._is_list_request(user_query):
            q_vec  = self.vectoriser.transform_query(user_query)
            scores = [
                TFIDFVectoriser.cosine_similarity(q_vec, self.tfidf_matrix[i])
                for i in range(len(self.chunks))
            ]
            ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)

            if scores[ranked[0]] >= self.SIMILARITY_THRESHOLD:
                items  = [i for i in ranked if scores[i] >= self.SIMILARITY_THRESHOLD]
                header = "Here are the matching tools from our Dinosaur List:"
            else:
                items  = ranked
                header = "Here are all the tools in our Dinosaur List:"

            lines = [header]
            for n, i in enumerate(items, 1):
                desc = self._extract_description(self.chunks[i])
                lines.append(f"\n{n}. {self.meta[i]['name']}  —  by {self.meta[i]['author']}")
                lines.append(f"   {desc}")
            lines.append("\nFeel free to ask me about any of them!")
            response = "\n".join(lines)
            if on_token:
                on_token(response)
            return response, False

        # ── Tool recommendation: TF-IDF top-1 + LLM ──────────────────────────
        # Gate 1: reject content-free queries — unless a known tool name is present
        if not self._has_meaningful_content(user_query) \
                and not self._has_tool_name_match(user_query):
            msg = (
                "I couldn't detect a specific keyword in your message.\n"
                "Please describe the task or problem you need help with — "
                "for example: 'band validation', 'data mapping', or 'GRR analysis'."
            )
            if on_token:
                on_token(msg)
            return msg, False

        results = self.retrieve(user_query, top_k=5)

        # Gate 2: no tool scored above threshold — do not recommend anything
        if not results:
            msg = (
                "I couldn't find a tool in the Dinosaur List that matches your request.\n"
                "Please try describing your need more specifically.\n"
                "You can also type 'list all tools' to browse what's available."
            )
            if on_token:
                on_token(msg)
            return msg, False

        self._last_tool = results[0]

        # ── Multiple matches: show them all as a list ──────────────────────
        if len(results) > 1:
            lines = [f"I found {len(results)} matching tools:\n"]
            for n, r in enumerate(results, 1):
                desc = self._extract_description(r["content"])
                lines.append(f"{n}. {r['name']}  —  by {r['author']}")
                lines.append(f"   {desc}")
            lines.append("\nType a tool name for details, or 'describe' for more on the top result.")
            msg = "\n".join(lines)
            if on_token:
                on_token(msg)
            return msg, False

        # ── Single match ────────────────────────────────────────────────────
        best = results[0]
        if self._is_elaborate_request(user_query):
            msg = self._build_describe_response(best)
        else:
            msg = self._build_brief_response(best)
        if on_token:
            on_token(msg)
        return msg, False

    # ── Backward-compat alias ─────────────────────────────────────────────────
    def load_readmes(self) -> bool:
        """Alias for load_and_index() — kept for backward compatibility."""
        return self.load_and_index()

    # ── Conversation loop ─────────────────────────────────────────────────────
    def run(self) -> None:
        """Start the interactive NLP-powered assistant."""
        print("=" * 60)
        print("  Hi, welcome to WSD Dinosaur List!  [NLP Edition]")
        print("  Powered by TF-IDF vector search + LLM generation.")
        print("  Describe what you need — I'll find the best match!")
        print("  (Type 'exit' or 'quit' to leave)")
        print("=" * 60)

        print("\n📂 Indexing README files from Source folder...\n")
        if not self.load_and_index():
            return

        print(f"\n✅ {len(self.chunks)} tool(s) indexed and ready!\n")

        while True:
            user_query = input(
                f"{self.BOLD_YELLOW}💬 What would you like to know today?\n> {self.RESET}"
            ).strip()

            if not user_query:
                continue

            if user_query.lower() in ("exit", "quit", "bye", "q"):
                print(
                    "\nThank you for using WSD Dinosaur List! "
                    "Have a wonderful day. Goodbye! 👋\n"
                )
                break

            # ── Intent branch ──────────────────────────────────────────────
            if self._is_small_talk(user_query):
                self.respond_chat(user_query)
                continue

            if self._is_list_request(user_query):
                # "list tools for X" → show ALL matches as a formatted list
                self.respond_list(user_query)
                continue

            # Single tool recommendation → TF-IDF top-1 + LLM generation
            print(f"\n{self.BOLD_CYAN}🔍 Searching Dinosaur List...{self.RESET}\n")
            not_found = self.respond(user_query)

            if not_found:
                print(
                    f"{self.BOLD_YELLOW}💡 Would you like me to write some code to help "
                    f"resolve this? (yes / no){self.RESET}"
                )
                answer = input("> ").strip().lower()
                if answer in ("yes", "y"):
                    print(
                        f"\n{self.BOLD_CYAN}🤖 Let me write some code for you..."
                        f"{self.RESET}\n"
                    )
                    self.respond_with_code(user_query)
                else:
                    print(
                        "\nNo problem at all! Feel free to describe what you need "
                        "and I'll do my best to help! 😊\n"
                    )


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot = DinosaurVectorBot()
    bot.run()
