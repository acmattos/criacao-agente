# Etapa 4 — Consulta seletiva ao regulamento (Garantia 4)

Implementa a consulta ao regulamento interno (`dados/regulamento.md`, 14 capítulos, 96 artigos) sem carregar o documento inteiro na sessão nem nas instruções do agente principal. Preserva todas as garantias e testes das etapas anteriores — nenhum deles foi alterado.

## O que foi implementado

- `aurora_assistant.regulamento`: parser que segmenta o markdown em artigos, cada um com capítulo + número preservados como identificação da fonte, e uma função `search(query)` de busca por palavra-chave (sem infraestrutura vetorial, conforme o plano original).
- `agent.consult_regulation(topic)`: tool nova, sem identidade de sessão (o regulamento não depende de apartamento), que devolve só os artigos relevantes — nunca o documento inteiro.
- `agent.regulation_specialist`: terceiro especialista (`sub_agents` de `main_agent`, mesma topologia das Etapas 1–3). Não tem tool de confirmação (consultar regulamento não cobra nem libera acesso), mas fica no mesmo grupo por consistência.
- `main_agent` não recebe o regulamento nas instruções — só sabe que existe um especialista para dúvidas sobre regras/normas. `regulation_specialist` também não: sua instrução só explica como usar a tool, nunca contém texto do regulamento.

## Por que a busca não é "qualquer coisa que bater uma palavra"

A primeira versão desta etapa usava sobreposição simples de palavras-chave entre a pergunta e cada artigo, e **vazou capítulos errados** numa validação real: perguntando sobre cachorro de grande porte, o modelo tentou algumas reformulações da busca, e uma delas (`"tamanho porte cachorro apartamento maximo"`) bateu, ao mesmo tempo, em artigos do Capítulo I (Disposições gerais), do Capítulo III (Silêncio) e do Capítulo IV (Piscina) — a palavra genérica "porte" aparece tanto em "animais de grande porte" (Capítulo VIII, o assunto certo) quanto em "boias de grande porte" (Capítulo IV, piscina), e "apartamento"/"máximo" bateram em frases de outros capítulos por coincidência.

**Correção** (`regulamento.search`):
1. Um artigo só entra na disputa se a pergunta bater com o **título do capítulo** dele (sinal direto de assunto) **ou** se pelo menos **duas palavras distintas** da pergunta aparecerem no corpo do artigo — uma única palavra genérica batendo por coincidência não basta sozinha.
2. Pontuação usa peso **IDF** (palavras raras no documento inteiro valem mais que palavras comuns) só para desempatar entre os artigos que já passaram no filtro acima.
3. O resultado fica restrito a **um único capítulo** — o de maior pontuação (pelo artigo que melhor pontua sozinho, nunca pela soma de vários artigos fracos) — nunca mistura assuntos na mesma resposta.
4. Sem nenhum artigo qualificado, devolve lista vazia; a instrução do `regulation_specialist` manda dizer que não encontrou, nunca inventar.

Com isso, a consulta antes problemática (`"tamanho porte cachorro apartamento maximo"`) passou a devolver uma lista vazia em vez de puxar o capítulo errado — "não encontrado" é a resposta seguinte mais segura, não "errado mas com confiança".

Testes de regressão dedicados (`tests/test_regulamento.py::test_search_never_mixes_chapters_in_one_response` e `test_search_prefers_not_found_over_coincidental_single_word_match`) fixam esse achado.

## Validação real (Gemini de verdade, API HTTP real)

Contra o servidor real (`gemini-flash-lite-latest`), sem restaurar os dados das etapas anteriores:

| Cenário | Resultado |
|---|---|
| Passo 12 do avaliador: "Até que horas a piscina funciona aos domingos?" (sessão com histórico de reservas/visitantes das etapas anteriores) | Resposta cita corretamente **9h às 20h** e o Art. 22, inciso II; evento `consult_regulation` devolveu só artigos do Capítulo IV (Piscina) |
| "Posso ter um cachorro grande no apartamento? Preciso usar coleira?" (antes da correção) | **Achado o vazamento**: uma das reformulações do modelo trouxe capítulos de Piscina, Disposições gerais e Silêncio misturados com Animais de estimação |
| Mesma pergunta, depois da correção | Resposta precisa (vacinação, cadastro, guia/coleira, focinheira, coleira refletiva, elevador de serviço); todas as chamadas de `consult_regulation` da sessão devolveram só o Capítulo VIII (Animais de estimação) |
| Pergunta sobre piscina de novo, na mesma sessão (depois de já ter perguntado sobre animais) | Resposta correta; chamada de `consult_regulation` isolada devolveu só Capítulo IV, sem reaparecer conteúdo de animais |

Em nenhuma chamada real — nem antes nem depois da correção — o **passo 12 do enunciado** (piscina) vazou conteúdo de outro capítulo; o vazamento encontrado foi numa pergunta diferente, que esta etapa testou por conta própria além do exigido.

## Testes determinísticos (sem Gemini)

`uv run pytest tests/` — **85 testes**, todos passando: 76 preservados das etapas anteriores (nenhuma mudança de comportamento) + 9 novos (`tests/test_regulamento.py`): parsing (96 artigos, numeração "Art. 1º" vs "Art. 10.", texto do artigo até o próximo marcador), busca da piscina, busca de animais, busca vazia para termos sem relação, limite de artigos, e as duas regressões do achado acima.

## O que fica para as próximas etapas (plano original)

- **Etapa 5** — validação integrada: repetir os 15 passos do avaliador em sequência única, do ambiente limpo à disputa final (etapas anteriores já validaram os passos 1–11 e 13–14 isoladamente; esta etapa valida o 12).
- **Etapa 6** — documentação e entrega: README final substituindo o enunciado (Arquitetura, Garantias, Como rodar).
