# Plano inicial — Assistente do Residencial Aurora

Status: proposta inicial para orientar um agente de software. Não representa implementação nem validação concluída.

## 1. Missão do agente

Implementar o desafio do Residencial Aurora no repositório fornecido, seguindo integralmente o enunciado original e seus 15 passos de avaliação. O modelo conduz a conversa; o código controla identidade, autorização, confirmação e efeitos persistidos.

Leia o README original e todos os arquivos de dados antes de programar. Preserve uma cópia do enunciado em `docs/challenge.md` antes de substituir o README de entrega. Este plano é complementar: em caso de conflito, prevalece o enunciado.

Decisão confirmada pelo usuário: seguir com Google ADK e Gemini. A aplicação será construída nessa stack, sem substituir o framework ou o provedor de modelos.

## 2. Restrições obrigatórias

- Python 3.12 ou superior; gerenciamento por uv; `pyproject.toml` e `uv.lock` versionados.
- Google ADK da série 2, versão igual ou superior a 2.2.0, com versão exata fixada. Consultar documentação e código-fonte correspondentes à versão escolhida.
- Gemini com chave do Google AI Studio; escolher modelo disponível no projeto e adequado às cotas. Não inventar identificadores nem presumir disponibilidade.
- Um agente principal e pelo menos dois especialistas reais do ADK.
- Reservas e visitantes operados por tools; rotas de verificação consultam o armazenamento diretamente.
- API em `http://localhost:8000`, com o contrato exato do enunciado.
- Nenhuma alteração nos bytes de `dados/`, inclusive formatação dos JSONs.
- `.env` não versionado; `.env.example` com nomes das variáveis, sem valores.
- Código e identificadores internos em inglês. Contrato HTTP e documentação em português conforme o enunciado; não traduzir campos como `apartamento` e `confirmado`.
- Entrega final em fork público, branch `main`, com README nas três seções exigidas.

Não acrescentar interface, autenticação real, pagamento, estorno, regras de antecedência, bloqueio de datas passadas, capacidade, deploy ou infraestrutura distribuída. Testes são uma recomendação deste plano, não uma exigência adicional do enunciado.

## 3. Dados já conferidos

| Área | ID | Taxa |
|---|---|---:|
| Salão de festas | salao-de-festas | 150,00 |
| Churrasqueira | churrasqueira | 80,00 |
| Quadra poliesportiva | quadra | 0,00 |

Há seis apartamentos, três reservas e duas autorizações iniciais. Os códigos existentes são `RSV-1377`, `RSV-4821` e `RSV-2950`. Eles não podem ser reutilizados após cancelamento.

O regulamento tem 14 capítulos e 96 artigos. O artigo 22, inciso II, informa que a piscina funciona aos domingos e feriados das 9h às 20h. Essa resposta deve resultar de consulta ao documento, não de texto fixo inserido no prompt. Piscina não consta no cadastro de áreas reserváveis.

## 4. Arquitetura candidata

Proposta: FastAPI, armazenamento local SQLite e integração com Runner/App do ADK. A escolha concreta do serviço de sessões e da topologia fica condicionada à prova técnica da etapa 1.

| Camada | Responsabilidade |
|---|---|
| HTTP | Contratos, validação de entrada e códigos de resposta |
| Aplicação | Casos de uso, identidade confiável e coordenação das operações |
| Domínio | Políticas de reserva, cancelamento, confirmação e resultados de negócio |
| Adaptador ADK | Agentes, tools, eventos, execução e retomada |
| Persistência | Transações, unicidade, códigos, dados e pendências |
| Consulta documental | Recuperação seletiva do regulamento |

Usar separação suficiente para testar regras sem chamar Gemini. Evitar hierarquias, interfaces e abstrações sem necessidade concreta.

Agentes propostos: principal, reservas, visitantes e regulamento. Três especialistas facilitam separar responsabilidades, mas dois são suficientes se houver justificativa. Não decidir entre transferência e agente como tool antes de validar retomada persistida.

## 5. Invariantes de implementação

### Identidade e isolamento

O apartamento é fixado na criação da sessão. Tools recebem a identidade por contexto confiável, sem parâmetro de apartamento escolhido livremente pelo modelo. Nenhuma tool pode mudar essa identidade.

Listagem e cancelamento sempre filtram pelo apartamento da sessão. Ao cancelar por código, verificar também a titularidade. Ao cancelar por área/data, restringir a consulta ao apartamento antes de devolver resultados.

Disponibilidade retorna apenas livre/ocupada. Não devolver registros de terceiros ao modelo, mesmo que a resposta final pudesse omiti-los. Eventos também não podem conter dados obtidos indevidamente. As rotas de verificação não devem ser expostas como tools administrativas irrestritas.

### Confirmação

Área com taxa positiva e autorização de visitante exigem confirmação. Quadra gratuita e cancelamento próprio não exigem. A taxa vem do cadastro confiável.

Vincular cada pendência à sessão, à operação, aos parâmetros apresentados e à correlação exigida pelo ADK. Usar a confirmação de tools do ADK e integrar sua retomada à API; não substituir o fluxo por um simples campo `approved` controlado pelo modelo.

Somente a rota de confirmações pode fornecer a decisão confiável. Texto como “já confirmei” não a substitui. Aprovar executa a operação correspondente uma única vez; negar encerra a pendência sem efeito de negócio. ID de outra sessão, inexistente ou já respondido produz 409.

Persistir o necessário para retomar pendências após reinício. Impedir reaplicação de efeitos por repetição interna da mesma operação; uma nova chamada após autorização não pode herdar autorização genérica. Registrar os estados e resultados de modo coerente com a transação de negócio. Não manter transação de escrita aberta durante chamadas ao modelo.

### Exclusividade e códigos

Garantir unicidade de área/data para reservas ativas no armazenamento. Uma opção é preservar canceladas e usar índice único parcial nas ativas. Conferência prévia de disponibilidade é apenas uma conveniência.

Converter conflito de unicidade em resultado de negócio sem detalhes do titular. No fluxo concorrente, ambas as confirmações retornam 200 e apenas uma reserva é criada. Não aplicar 409 à perda da disputa por disponibilidade.

Preservar códigos cancelados; garantir unicidade de código também no banco. Caso use códigos aleatórios, tratar colisão em vez de depender apenas de sua baixa probabilidade.

Uma pendência não ocupa definitivamente a área: dois moradores precisam poder obter pendências para a mesma área/data antes de aprová-las.

### Persistência e regulamento

Persistir eventos completos, estado necessário da sessão, reservas, cancelamentos, visitantes e pendências. A subida normal nunca restaura os dados automaticamente.

Ler/indexar o regulamento no servidor é permitido; enviar o documento inteiro ao modelo ou gravá-lo integralmente nos eventos não é. Recuperar artigos pertinentes, incluindo contexto necessário, antes de retornar a tool. Não resolver a garantia filtrando ou ocultando eventos na rota de consulta.

## 6. Etapas de execução

### Etapa 0 — Preparação

1. Ler o enunciado, inspecionar dados e registrar hashes de `dados/`.
2. Preservar o enunciado e criar a estrutura mínima do projeto.
3. Configurar uv, dependências fixadas, variáveis e exclusões de arquivos de execução.
4. Registrar decisões provisórias e comandos de execução.

Concluída quando: instalação é reproduzível e os dados originais permanecem intactos.

### Etapa 1 — Prova técnica de confirmação persistida

1. Criar principal e especialista com uma tool que exige confirmação.
2. Usar persistência desde o início, sem validar somente em memória.
3. Expor o pedido pela API e correlacionar corretamente sua resposta ao evento e à execução do ADK.
4. Comprovar aprovação, negativa e reenvio sequencial.
5. Reiniciar com confirmação pendente e comprovar a retomada pelo agente correto.
6. Confirmar que o efeito ocorreu uma única vez e os eventos foram preservados.

Concluída quando: há evidência de execução real com Gemini na versão fixada. HTTP 200 sem efeito não é sucesso. Se falhar, investigar topologia, transferência, retomada e serviço de sessão antes de expandir o projeto. Não afirmar compatibilidade não demonstrada.

### Etapa 2 — Persistência e regras

1. Implementar carga inicial e comando explícito de restauração.
2. Implementar listagem própria, disponibilidade, criação e cancelamento de reserva e listagem/autorização de visitantes.
3. Adicionar restrições transacionais e preservação de códigos.
4. Implementar consumo de pendências e prevenção de repetição de efeitos.

Concluída quando: testes determinísticos demonstram isolamento, cancelamento sem perda do código, ausência de efeito antes da confirmação e exclusividade na gravação.

### Etapa 3 — API completa e agentes

Implementar todos os contratos abaixo e conectar tools aos serviços de negócio. Retornar todas as pendências atuais, não apenas as geradas pela última chamada.

| Método e caminho | Contrato essencial |
|---|---|
| POST /sessoes | Entrada `apartamento`; 201 com `session_id` |
| POST /sessoes/{session_id}/mensagens | Entrada `texto`; 200 com `resposta` e `confirmacoes_pendentes` |
| POST /sessoes/{session_id}/confirmacoes | Entrada `id`, `confirmado`; 200 no formato de mensagens ou 409 para pendência inválida |
| GET /sessoes/{session_id}/eventos | 200 com todos os eventos completos e ordenados |
| GET /apartamentos/{apartamento}/reservas | 200 com lista de `codigo`, `area`, `data` |
| GET /apartamentos/{apartamento}/visitantes | 200 com lista de `nome`, `data` |

Todas as rotas com session_id retornam 404 se a sessão não existir. `resposta` pode ser vazia durante espera de confirmação. Datas de verificação seguem AAAA-MM-DD. Consultar o enunciado para exemplos integrais.

Concluída quando: os fluxos de reserva gratuita, paga, negativa, cancelamento, visitante e tentativa de acesso a outro apartamento funcionam pela API.

### Etapa 4 — Consulta do regulamento

1. Segmentar por capítulos/artigos preservando identificação da fonte.
2. Implementar seleção por assunto; começar simples, sem infraestrutura vetorial obrigatória.
3. Testar perguntas variadas, incluindo a piscina aos domingos, sem codificar apenas a frase do avaliador.
4. Inspecionar eventos de todos os agentes para detectar conteúdo de assuntos não consultados.

Concluída quando: a resposta informa fechamento às 20h a partir da consulta e os eventos não contêm capítulos irrelevantes.

### Etapa 5 — Validação integrada

Executar os 15 passos do avaliador em ordem, desde restauração inicial até disputa concorrente. Variar algumas mensagens sem alterar a intenção. Conferir efeitos pelas rotas de verificação e inspecionar eventos, além do texto final.

Separar evidências de testes com mocks das evidências com Gemini real. Mocks não comprovam transferência, solicitação ou retomada do ADK.

Concluída quando: todos os passos passam; os dois pedidos concorrentes possuem pendências antes das aprovações; ambas as aprovações retornam 200 e a soma das reservas é um.

### Etapa 6 — Entrega

1. Substituir README pelas seções Arquitetura, Garantias e Como rodar.
2. Apontar arquivos e símbolos/trechos existentes que implementam cada garantia.
3. Documentar instalação, variáveis, restauração e subida; reproduzir a execução em ambiente limpo.
4. Conferir hashes de dados, ausência de segredos, lockfile e versão exata do ADK.
5. Deixar as alterações prontas para entrega no fork público/main e informar qualquer ação de publicação ainda pendente.

Não declarar publicação, testes ou comandos como concluídos sem executá-los.

## 7. Matriz mínima de verificação

| Grupo | Evidência esperada |
|---|---|
| Instalação/restauração | Registros iniciais corretos; uv sync funciona |
| Identidade | Sessão 101 não consulta nem altera registros de 302; eventos não vazam os dados |
| Operações sem confirmação | Quadra criada e reserva própria cancelada sem pendência |
| Operações com confirmação | Taxa positiva e visitante aguardam a rota apropriada |
| Negativa/repetição | Negativa não grava; ID inválido, alheio ou respondido retorna 409 |
| Reinício | Mesma quantidade de eventos antes de nova mensagem; sessão continua; efeitos preservados |
| Regulamento | Piscina fecha às 20h; somente trechos pertinentes nos eventos |
| Concorrência | Duas aprovações 200; uma única reserva ativa |
| Integridade da entrega | Dados intactos, sem segredos, README rastreável e dependências fixadas |

O passo 14 é requisito do avaliador. Aprovar uma pendência após reinício é uma verificação adicional recomendada pelo próprio texto das dicas finais.

## 8. Decisões provisórias e riscos

- Restauração: proposta de apagar também sessões e pendências, com API parada, documentando o comportamento.
- Mensagem durante pendência: proposta de não iniciar nova operação; retornar as pendências atuais sem executar nada indevidamente.
- Apartamento inexistente: rejeitar de forma consistente, documentando o comportamento escolhido.
- Duas respostas simultâneas à mesma confirmação: não ampliar o escopo além da liberdade concedida; reenvio sequencial continua obrigatório.
- Modelo e ADK exatos: decidir na etapa 1, verificando disponibilidade, documentação e comportamento real.
- Ausência de chave ou cota: prosseguir nos testes locais e declarar precisamente o que não foi validado com Gemini.
- Consistência entre sessão e negócio: não presumir transação única entre serviços diferentes; documentar como a correlação e a prevenção de repetição mantêm efeitos coerentes.

## 9. Instrução para iniciar o agente

Use este plano e o enunciado como base. Comece pelas etapas 0 e 1 e avance pelas demais quando os critérios estiverem demonstrados. Não peça confirmação para escolhas rotineiras já delimitadas; registre a decisão e sua justificativa. Pergunte apenas sobre impedimentos reais ou mudanças materiais de escopo.

Ao concluir cada etapa, informe: alterações realizadas, comandos executados, resultado observado, riscos ainda abertos e próximo passo. Não apresente suposições como fatos e não declare sucesso somente porque a API respondeu sem erro.

Não substitua regras por instruções ao modelo, não altere os dados originais, não simule Gemini como solução final e não acrescente funcionalidades fora de escopo. Priorize primeiro a confirmação persistida, depois as regras de negócio e o contrato, e finalmente a reprodução integral do avaliador.
