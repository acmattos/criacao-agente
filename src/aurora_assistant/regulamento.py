"""Consulta seletiva ao regulamento interno (Etapa 4, Garantia 4).

O regulamento e longo (14 capitulos, 96 artigos). Carrega-lo inteiro no
historico da sessao, ou nas instrucoes do agente principal, faria ele
acompanhar toda mensagem seguinte e encarecer cada chamada ao modelo - por
isso a consulta e seletiva: `search` devolve so os artigos relevantes para
uma pergunta especifica, nunca o documento inteiro.

Busca por palavra-chave simples (sobreposicao de termos, sem acentuacao),
suficiente para o escopo do desafio: "comecar simples, sem infraestrutura
vetorial obrigatoria" (PLANO_INICIAL_AURORA.md, Etapa 4). Cada artigo
carrega a identificacao da fonte (capitulo + numero do artigo) para quem
consome o resultado poder citar de onde veio.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from . import config

_CHAPTER_RE = re.compile(r"^##\s*(Cap[ií]tulo[^\n]*)", re.MULTILINE)
_ARTICLE_RE = re.compile(r"\*\*(Art\.\s*\d+(?:º|\.))\*\*")

_STOPWORDS = {
    "a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "que", "um",
    "uma", "uns", "umas", "para", "com", "em", "no", "na", "nos", "nas",
    "se", "ou", "por", "ao", "aos", "sua", "seu", "suas", "seus", "nao",
    "nunca", "sempre", "como", "mais", "menos", "ate", "qual", "quais",
    "quando", "onde", "ser", "tem", "sao", "esta", "este", "esta", "isso",
}


@dataclass(frozen=True)
class Article:
    chapter: str
    number: str  # "Art. 22."
    text: str  # bloco completo (cabecalho + corpo + paragrafos/incisos)


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return text.lower()


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z]+", _normalize(text))
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


@lru_cache
def _load_articles() -> tuple[Article, ...]:
    return _parse(config.REGULAMENTO_PATH.read_text(encoding="utf-8"))


def _parse(content: str) -> tuple[Article, ...]:
    markers: list[tuple[str, int, str]] = []
    for m in _CHAPTER_RE.finditer(content):
        markers.append(("chapter", m.start(), m.group(1).strip()))
    for m in _ARTICLE_RE.finditer(content):
        markers.append(("article", m.start(), m.group(1)))
    markers.sort(key=lambda t: t[1])

    articles: list[Article] = []
    current_chapter = ""
    for i, (kind, start, label) in enumerate(markers):
        if kind == "chapter":
            current_chapter = label
            continue
        end = next(
            (pos for (_, pos, _) in markers[i + 1:] if pos > start), len(content)
        )
        articles.append(Article(chapter=current_chapter, number=label, text=content[start:end].strip()))
    return tuple(articles)


@lru_cache
def _document_frequency() -> dict[str, int]:
    """Em quantos artigos cada palavra aparece (titulo do capitulo + corpo).
    Usado para pesar menos palavras genericas do condominio (ex.
    "apartamento", "morador", que aparecem por toda parte) e mais palavras
    raras e especificas de um unico assunto (ex. "piscina")."""
    df: dict[str, int] = {}
    for article in _load_articles():
        for word in _tokenize(article.chapter) | _tokenize(article.text):
            df[word] = df.get(word, 0) + 1
    return df


def _idf(word: str) -> float:
    df = _document_frequency()
    total = len(_load_articles())
    freq = df.get(word, 0)
    return math.log((total + 1) / (freq + 1)) + 1.0


def search(query: str, *, max_articles: int = 3) -> list[Article]:
    """Devolve os artigos mais relevantes para `query`, restritos a UM SO
    capitulo - o de maior pontuacao - nunca misturando assuntos diferentes
    na mesma resposta.

    Um artigo so entra na disputa se a pergunta bater com o TITULO do
    capitulo dele (sinal direto de assunto, ex. "piscina" -> capitulo
    "Piscina") OU se pelo menos DUAS palavras distintas da pergunta
    aparecerem no corpo do artigo. Uma unica palavra generica batendo por
    coincidencia num artigo de outro assunto (ex. "porte" aparecendo tanto
    em "Piscina" quanto em "Animais de estimação") nao basta sozinha -
    isso e o que evita puxar o capitulo errado so por uma coincidencia
    lexical. Pontuacao usa peso IDF (palavras raras no documento pesam mais
    que palavras comuns) para desempatar entre artigos que passaram no
    filtro. Sem nenhum artigo qualificado, devolve lista vazia - nunca
    "qualquer coisa" so para preencher uma resposta.
    """
    query_words = _tokenize(query)
    if not query_words:
        return []

    article_scores: dict[Article, float] = {}
    for article in _load_articles():
        chapter_hits = query_words & _tokenize(article.chapter)
        body_hits = query_words & _tokenize(article.text)
        if not chapter_hits and len(body_hits) < 2:
            continue
        score = sum(_idf(w) for w in chapter_hits) * 3 + sum(_idf(w) for w in body_hits)
        article_scores[article] = score

    if not article_scores:
        return []

    chapter_scores: dict[str, float] = {}
    for article, score in article_scores.items():
        # Maximo, nao soma: um capitulo so deve vencer por ter UM artigo que
        # bate bem sozinho, nunca por acumular varios artigos fracos.
        chapter_scores[article.chapter] = max(chapter_scores.get(article.chapter, 0.0), score)

    best_chapter = max(chapter_scores, key=chapter_scores.get)
    candidates = [a for a in article_scores if a.chapter == best_chapter]
    candidates.sort(key=lambda a: article_scores[a], reverse=True)
    return candidates[:max_articles]
