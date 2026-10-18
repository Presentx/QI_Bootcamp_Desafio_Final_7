from fastapi import FastAPI, Request

from database import clear_context, open_context


def register_session_manager_middleware(application: FastAPI) -> None:
    """Cuida da conversa com o banco do começo ao fim de cada requisição.

    Quem fala com o banco fala por uma *sessão*: um rascunho onde as
    mudanças ficam guardadas até alguém mandar salvar. Ela precisa nascer
    no início da requisição e morrer no fim — sempre, inclusive quando a
    rota explode no meio.

    Quem cuidava disso era a dependency `get_db`, em src/database.py.
    Agora é este middleware, e o motivo da troca não é técnico: é que
    **middleware é o padrão que todo mundo já conhece**. Numa API que
    você abre pela primeira vez, "onde a sessão nasce e morre?" é uma
    pergunta que se responde olhando o bloco de middlewares do
    src/app.py. É lá que as pessoas procuram, e é bom que a resposta
    esteja lá. O custo dessa escolha está em src/database.py.

    ────────────────────────────────────────────────────────────────
    O COMBINADO, EM TRÊS LINHAS
    ────────────────────────────────────────────────────────────────
    • **rollback** se a requisição terminou em exceção: o que ficou pela
      metade é rasgado, e não sobra meia entidade no banco.
    • **close** SEMPRE, no `finally`. Conexão que não é devolvida é
      conexão que falta pra próxima pessoa, e é assim que uma API para de
      responder sem ninguém entender por quê.
    • **commit NUNCA** — a próxima seção é só sobre isto.

    ────────────────────────────────────────────────────────────────
    POR QUE ELE NÃO DÁ COMMIT
    ────────────────────────────────────────────────────────────────
    Seria fácil salvar no caminho de sucesso: a requisição respondeu 200,
    então commita. E seria errado.

    Quem sabe se o trabalho terminou é o controller, não a camada de
    fora. Abra src/controllers/pix_transaction_controller.py: em `analyze` e
    em `register_two_factor`, o commit (`_commit`) é a ÚLTIMA coisa antes
    do `return` — depois que a regra passou, nunca antes.

    Com o commit aqui, esse controle sumiria. Uma rota que grava e depois
    descobre que não podia teria a gravação salva assim mesmo, porque a
    exceção nasceria DEPOIS do commit. E ninguém mais conseguiria dizer,
    lendo o controller, em que ponto exato o dado foi pro disco.

    O combinado é: o middleware cuida do CICLO DE VIDA, o controller
    decide o CONTEÚDO. Salvar é decisão de conteúdo.

    ────────────────────────────────────────────────────────────────
    "E o /health_check? Ele não usa banco."
    ────────────────────────────────────────────────────────────────
    Essa é a objeção certa, e é o preço de trazer a sessão pra cá: a
    dependency só era chamada por quem pedia, e o middleware atende todo
    mundo. O Docker consulta o /health_check a cada três segundos, e
    seria ruim que cada checagem tomasse uma conexão emprestada por nada
    — pior ainda: o health check passaria a depender do banco estar de
    pé, e uma lentidão no banco derrubaria um container que ainda estava
    atendendo.

    Havia dois caminhos:

    1. **Uma lista de exceções**, como a BYPASS_ENDPOINTS que o
       internal_token e o request_logger usam.
    2. **Não abrir nada até alguém pedir** — que é o que está escrito
       aqui embaixo: o middleware só abre o contexto vazio, e quem
       cria a sessão, se algum controller pedir, é o próprio contexto.

    Ficou o segundo, por dois motivos. O primeiro é que a
    BYPASS_ENDPOINTS responde a OUTRA pergunta: ela diz "esta rota é
    pública", não "esta rota não usa banco". Hoje as duas listas seriam
    iguais; no dia em que uma rota pública precisar do banco, ela entra
    lá pelo motivo certo e perde a sessão pelo motivo errado — e o erro
    aparece longe daqui. Criar uma segunda lista, quase igual à primeira,
    só muda qual das duas alguém vai esquecer de atualizar.

    O segundo é que a resposta já está escrita num lugar melhor: no
    próprio código que vai usar o banco. Construir um controller é dizer
    "eu uso banco" — e o `/` e o /health_check não constroem nenhum. Não
    há lista pra manter, e rota nova acerta sozinha.

    Repare no que se perdeu nessa troca, porque é honesto dizer: antes a
    DECLARAÇÃO estava na assinatura da rota, à vista de quem lesse só o
    arquivo de rotas. Agora ela está um andar mais fundo. Ganhamos uma
    lista a menos e pagamos com um salto a mais pro leitor.

    ────────────────────────────────────────────────────────────────
    A ARMADILHA: SESSÃO QUE PODE NÃO EXISTIR
    ────────────────────────────────────────────────────────────────
    Os dois caminhos acima cobram o mesmo preço, e ele está nas duas
    perguntas `if context.db_session is not None` logo abaixo. Elas parecem
    burocracia e não são: sem elas, a API quebra. Este projeto rodou o
    erro de propósito, com a lista de exceções e um `finally`
    desprotegido:

        finally:
            context.db_session.close()

        GET /                    -> 500
        GET /health_check        -> 500
        GET /sample_entities     -> 200
        POST /pix/transaction    -> 201

        AttributeError: 'NoneType' object has no attribute 'close'

    Leia de novo a ordem dos quatro resultados, porque ela é o ensinamento
    inteiro. Você mexeu na SESSÃO DE BANCO — e quem quebrou foi justo
    quem NÃO usa banco. As rotas que você foi conferir primeiro ficaram
    verdes, e o 500 foi parar no health check, que é o endereço que o
    Docker consulta pra decidir se o container está vivo.

    Toda vez que a sessão é opcional, existe um segundo caminho de
    requisição em que ela não existe, e todo `rollback`/`close` precisa
    perguntar antes de agir. A diferença entre os dois desenhos não é o
    custo — é a VISIBILIDADE dele:

    • Com a lista de exceções, quem decide que não há sessão é uma lista
      em OUTRO arquivo (src/constants.py). Aqui embaixo, o `finally`
      parece que sempre tem uma sessão pra fechar. Nada te lembra.
    • Do jeito preguiçoso, o próprio middleware escreve `= None` na linha
      de cima. É impossível escrever o `finally` sem esbarrar nela.

    Duas perguntas à vista valem mais que uma lista que você não vê.

    Uma honestidade a mais, porque a lista de exceções costuma ser
    vendida como uma economia maior do que é: `SessionLocal()` não
    conecta no banco. O SQLAlchemy só pega uma conexão do pool no
    primeiro comando SQL de verdade. Mesmo sem nada disto, o custo do
    /health_check seria criar e jogar fora um objeto Python. O que a
    preguiça compra não é velocidade — é a garantia de que uma rota que
    não fala com o banco não **dependa** do banco.

    ────────────────────────────────────────────────────────────────
    ONDE ELE FICA NA PILHA
    ────────────────────────────────────────────────────────────────
    É o mais INTERNO dos middlewares, encostado nas rotas — e por isso é
    o PRIMEIRO registrado no src/app.py, não o último (o comentário de lá
    explica por que a lista de registro é o espelho do diagrama).
    Requisição sem o INTERNAL-TOKEN leva 403 na camada de cima e nem
    chega aqui: quem não passou na porta não precisa de uma sessão de
    banco.

    Duas consequências dessa posição que valem saber:

    • O `except` daqui só vê o erro INESPERADO. Os erros previstos deste
      projeto (o 404 de entidade que não existe, por exemplo) viram
      resposta mais pra dentro, num exception handler, e chegam aqui como
      uma resposta normal. Está certo assim: erro previsto é tratado pelo
      controller antes de sujar coisa nenhuma.
    • Quando o `finally` roda, a rota já terminou e a resposta já está
      montada — mas ela ainda não saiu pela rede. Toda resposta desta API
      é montada inteira antes de sair, então fechar aqui é seguro. Uma
      rota que devolvesse um fluxo lendo do banco aos poucos encontraria
      a sessão já fechada, e essa rota precisaria de outro desenho.

    E há um detalhe que fecha o quadro, porque ele contraria o que este
    arquivo dizia até pouco tempo atrás. Middleware realmente não tem
    como ENTREGAR um objeto para a rota — isso continua sendo verdade.
    Mas ele não precisa entregar: basta DEIXAR num lugar combinado, e
    quem vier depois pega. O lugar é o contexto (`contextvars`), o mesmo
    caminho por onde o `request_id` deste projeto já viajava.

    Por isso a linha aqui embaixo abre um contexto em vez de escrever no
    `request.state`: o contexto é um lugar combinado, que qualquer camada
    mais funda alcança sem que ninguém precise passar nada adiante.

    O resto da história está em src/database.py.
    """

    @application.middleware("http")
    async def manage_session(request: Request, call_next):
        # A requisição começa sem sessão nenhuma, e pode terminar assim.
        # Quem cria é o controller, ao pedir a sessão ao contexto.
        context = open_context()

        try:
            response = await call_next(request)
        except Exception:
            # Explodiu depois de a rota já ter mexido em alguma coisa: o
            # rascunho é rasgado aqui. Sem isto, a conexão voltaria pro
            # pool com uma transação abortada em cima dela, e a PRÓXIMA
            # requisição a pegar essa conexão levaria o erro de uma
            # requisição que não é a dela.
            #
            # Na prática o `close` logo abaixo também desfaria — ele
            # nunca devolve uma transação pendente pro pool. Esta linha
            # fica assim mesmo, dizendo a intenção em voz alta: o dia em
            # que alguém trocar o que vem depois, o combinado continua
            # escrito.
            if context.db_session is not None:
                context.db_session.rollback()
            raise
        finally:
            # Sempre. Deu certo, deu 404, explodiu: a conexão volta pro
            # pool nesta linha. E repare na pergunta antes do ponto: ela
            # é a guarda da armadilha contada na docstring.
            if context.db_session is not None:
                context.db_session.close()

            # E o contexto sai de circulação. Fora de uma requisição,
            # pedir o contexto tem que falhar alto — não devolver o desta
            # aqui, com a sessão já fechada.
            clear_context()

        return response
