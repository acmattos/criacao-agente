from __future__ import annotations

from aurora_assistant import regulamento


def test_parses_all_96_articles():
    articles = regulamento._load_articles()
    assert len(articles) == 96


def test_parses_chapter_titles_and_low_and_high_numbered_articles():
    articles = {a.number: a for a in regulamento._load_articles()}
    assert "Art. 1º" in articles
    assert articles["Art. 1º"].chapter == "Capítulo I: Disposições gerais"
    assert "Art. 22." in articles
    assert articles["Art. 22."].chapter == "Capítulo IV: Piscina"
    assert "Art. 96." in articles
    assert articles["Art. 96."].chapter == "Capítulo XIV: Disposições finais"


def test_article_text_includes_paragraphs_until_next_article():
    articles = {a.number: a for a in regulamento._load_articles()}
    art_22 = articles["Art. 22."]
    assert "9h às 20h" in art_22.text
    assert "Parágrafo único" in art_22.text
    # nao vaza para o proximo artigo.
    assert "Art. 23" not in art_22.text


def test_search_pool_hours_returns_only_pool_chapter():
    results = regulamento.search("Até que horas a piscina funciona aos domingos?")
    assert results, "esperava pelo menos um artigo relevante"
    assert all(a.chapter == "Capítulo IV: Piscina" for a in results)
    assert any("9h às 20h" in a.text for a in results)


def test_search_pets_returns_only_pets_chapter():
    results = regulamento.search("Quais sao as regras para animais de estimacao no condominio?")
    assert results
    assert all(a.chapter == "Capítulo VIII: Animais de estimação" for a in results)


def test_search_unrelated_terms_can_return_empty():
    results = regulamento.search("xpto qwzy blerg nonexistent gibberish")
    assert results == []


def test_search_limits_number_of_articles():
    results = regulamento.search("morador unidade condominio", max_articles=2)
    assert len(results) <= 2


def test_search_never_mixes_chapters_in_one_response():
    """Achado real: consultas com termos genericos (ex. 'porte', 'maximo',
    'apartamento') batiam por coincidencia em artigos de capitulos
    diferentes (Piscina, Disposicoes gerais, Silencio) ao mesmo tempo que
    em Animais de estimacao, misturando assuntos numa so resposta - viola
    a Garantia 4. Toda chamada de search() so pode devolver artigos de UM
    capitulo."""
    queries = [
        "animais de estimacao caes porte coleira areas comuns",
        "porte cachorro peso limite apartamento animais",
        "Ate que horas a piscina funciona aos domingos?",
        "morador unidade condominio",
    ]
    for query in queries:
        results = regulamento.search(query)
        chapters = {a.chapter for a in results}
        assert len(chapters) <= 1, f"query {query!r} misturou capitulos: {chapters}"


def test_search_prefers_not_found_over_coincidental_single_word_match():
    """Mesmo achado acima: uma unica palavra generica (ex. 'maximo', que so
    aparece em Art. 27 da Piscina por coincidencia - limite de convidados)
    nao pode, sozinha, puxar um capitulo errado para uma pergunta sobre
    outro assunto. Preferir devolver vazio a devolver o capitulo errado."""
    results = regulamento.search("tamanho porte cachorro apartamento maximo")
    assert results == [] or all(a.chapter == "Capítulo VIII: Animais de estimação" for a in results)
