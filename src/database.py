"""Onde a sessão de banco mora, e como ela chega em quem precisa dela.

────────────────────────────────────────────────────────────────────
"Este arquivo não tinha um `get_db`?"
────────────────────────────────────────────────────────────────────
Tinha, e a história dele é a melhor coisa que este arquivo tem para
ensinar — porque são três desenhos, cada um trocando um problema por
outro, e nenhum de graça.

**Primeiro desenho: a dependency.** O `get_db` abria a sessão, entregava
com `yield`, fazia `rollback` se a rota explodisse e `close` no
`finally`. O ciclo de vida inteiro morava aqui, e a rota pedia assim:

    def minha_rota(db: Session = Depends(get_db)):

A favor: a rota DECLARAVA que usava banco, e quem não pedisse não abria
conexão nenhuma.

**Segundo desenho: o ciclo virou middleware.** "Onde a sessão nasce e
morre?" é uma pergunta que se responde olhando a lista de middlewares —
é lá que as pessoas procuram. O `get_db` encolheu para um balcão de
retirada: a sessão ficava no `request.state`, e ele a devolvia.

**Terceiro desenho, que é o de hoje: o `get_db` acabou.** A rota não
pede mais nada; quem pede é o controller, ao ser construído.

────────────────────────────────────────────────────────────────────
O QUE MUDOU DE VERDADE — "entregar" virou "deixar onde dá pra achar"
────────────────────────────────────────────────────────────────────
A defesa do desenho anterior era esta frase: *middleware não consegue
passar objeto pra rota, só a dependency consegue*. A frase continua
verdadeira, e mesmo assim o `get_db` morreu. Vale entender por quê,
porque é a ideia inteira deste arquivo.

Middleware continua sem conseguir **entregar** nada. O que ele consegue
é **deixar num lugar onde quem vier depois sabe procurar** — e esse
lugar é o `contextvars`, o mesmo mecanismo que o `request_id` deste
projeto já usava antes (src/utils/request_context.py). Não é um
truque novo: é o caminho que você já viu funcionando no bloco do log.

Entregar é empurrar; deixar no contexto é pousar. Para quem recebe, dá
no mesmo — e o middleware sabe fazer o segundo.

────────────────────────────────────────────────────────────────────
O QUE ISSO CUSTA — três coisas, e nenhuma é de graça
────────────────────────────────────────────────────────────────────
• **A sessão virou ambiente.** Antes, a assinatura da rota dizia em voz
  alta "eu uso banco". Agora a sessão está no ar, e qualquer código, em
  qualquer camada, alcança o banco chamando `get_context()` — inclusive
  um DTO, que não deveria. Isso é uma perda real de legibilidade, e não
  há como impedir por código: o combinado é que **só o controller
  chama `get_context()`**, e é curto de propósito, pra caber na cabeça.

• **Duas peças precisam concordar.** Quem prepara o contexto é o
  middleware, no começo de cada requisição. Se ele não rodar, o
  `get_context` levanta. Isto aqui MELHOROU em relação
  ao desenho anterior, que quebrava com um `AttributeError` seco longe
  do crime: agora a mensagem diz qual peça faltou e onde ela mora.

• **O ciclo é mais curto que o da dependency.** A dependency fechava a
  sessão depois de a resposta estar pronta; o middleware fecha assim que
  a rota devolve. Para esta API dá no mesmo — toda resposta daqui é
  montada inteira antes de sair. Uma rota que devolvesse um fluxo lendo
  do banco aos poucos encontraria a sessão já fechada, e é o tipo de
  rota que pediria outro desenho.

────────────────────────────────────────────────────────────────────
UMA CONTA QUE ESTE PROJETO JÁ ERROU
────────────────────────────────────────────────────────────────────
Por muito tempo estava escrito aqui que a preguiça do `if` (só abrir a
sessão quando alguém pede) era o que mantinha "o /health_check sem
conexão nenhuma". Isso era **falso**, e vale corrigir em voz alta em vez
de apagar em silêncio.

`SessionLocal()` não conecta no banco. O SQLAlchemy só tira uma conexão
do pool no primeiro comando SQL de verdade. Medido, com o pool a
descoberto:

    depois de SessionLocal()   -> 0 conexões em uso
    depois do PRIMEIRO SQL     -> 1 conexão em uso
    depois do close()          -> 0 conexões em uso

Ou seja: o /health_check não abriria conexão nenhuma de qualquer jeito,
porque ele não emite SQL. A preguiça não economiza conexão.

O que ela compra, então? Duas coisas menores e uma grande. As menores:
um objeto Python que não é criado, e a garantia de que uma rota que não
fala com o banco não **dependa** do banco estar de pé. A grande é a
lição: enquanto a sessão for opcional, todo `close` e todo `rollback`
precisa perguntar antes de agir.

Ensinar um custo que não existe é pior do que não ensinar nada.
"""

from contextvars import ContextVar
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from constants import DATABASE_URL


engine = create_engine(DATABASE_URL, pool_size=5, pool_recycle=600, pool_pre_ping=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False)


class Context:
    """O contexto de um trabalho: o que vale pra esta requisição, e só pra ela.

    Hoje ele carrega uma coisa só — a sessão de banco. É o mesmo objeto
    que existe nos serviços da QI (lá em `utils/context.py`), e lá ele
    carrega mais: o identificador da requisição, o corpo cru, o tempo.
    Neste projeto o identificador ainda viaja por conta própria, em
    src/utils/request_context.py; juntar os dois é o próximo passo
    natural, e não foi dado ainda.

    Começa vazio e só abre a sessão se pedirem.

    Por que um objeto, e não a sessão guardada direto no contexto? Porque
    o contexto só viaja num sentido. Quem cria a task filha (o middleware)
    passa uma CÓPIA do contexto pra baixo: a rota enxerga o que o
    middleware pôs, mas um `.set()` feito na rota é invisível lá em cima.
    Este projeto mediu:

        rota enxerga o que o middleware pôs         -> sim
        middleware enxerga o `.set()` feito na rota -> NAO
        middleware enxerga a MUTAÇÃO deste objeto   -> sim

    A leitura do meio é a que decide o desenho. Com a sessão guardada
    direto no contexto, quem a criasse seria a rota — e o `finally` do
    middleware acharia `None` pra sempre, fechando nada. Uma conexão
    vazada por requisição, em silêncio, até a décima sexta pendurar por 30
    segundos e virar 500.

    Guardando um objeto MUTÁVEL, as duas pontas olham o MESMO balcão: a
    rota mexe no atributo, o middleware lê o atributo. É o mesmo mecanismo
    do `request.state` de antes — um saco compartilhado —, só que este
    também serviria a quem não tem requisição nenhuma.
    """

    def __init__(self) -> None:
        self.db_session: Optional[Session] = None

    def get_or_create_session(self) -> Session:
        if self.db_session is None:
            self.db_session = SessionLocal()

        return self.db_session


_context: ContextVar[Optional[Context]] = ContextVar("context", default=None)


def open_context() -> Context:
    """Prepara o lugar da sessão deste trabalho e devolve o balcão.

    Chamada num lugar só: src/middlewares/session_manager.py, no começo
    de cada requisição. É o ponto de entrada da aplicação — o lugar onde
    um trabalho começa. Quem chama isto é quem também vai fechar a sessão
    no fim; abrir sem fechar é vazar conexão.
    """
    context = Context()
    _context.set(context)

    return context


def clear_context() -> None:
    """Tira o contexto de circulação. Chamada pelos mesmos dois lugares, no fim.

    Sem esta linha, fora de um trabalho o `get_context` devolveria o contexto
    do trabalho ANTERIOR, com a sessão já fechada. E sessão fechada do
    SQLAlchemy não reclama: ela reabre sozinha na próxima query, tomando
    uma conexão que ninguém mais fecharia.

    Na API isso quase não apareceria — cada requisição chega num contexto
    novo. Num programa de laço eterno, que pegasse um trabalho atrás do
    outro no mesmo processo, apareceria sempre.
    """
    _context.set(None)


def get_context() -> Context:
    """Devolve o contexto deste trabalho.

    Quem chama é o BaseController, ao ser construído. É esta função que
    substituiu o `db: Session = Depends(get_db)` que ficava na assinatura
    de cada rota.

    Ele devolve o CONTEXTO, não a sessão — e essa diferença é o motivo de
    o repository receber `context` e não `db`. Quem precisa do banco pede
    a sessão ao contexto (`context.db_session`); quem precisar de outra
    coisa que passe a viajar aqui dentro amanhã pede sem que nenhuma
    assinatura mude no caminho.

    A sessão continua preguiçosa: ela nasce no `get_or_create_session`,
    que o BaseController chama. Rota que não constrói controller nenhum —
    o `/` e o /health_check — não chega aqui, e por isso não depende do
    banco estar de pé.
    """
    context = _context.get()

    if context is None:
        raise Exception(
            "Nao existe contexto neste trabalho. "
            + "Quem prepara o contexto de cada requisicao e o middleware "
            + "src/middlewares/session_manager.py. Se voce chegou aqui, ele nao rodou."
        )

    return context
