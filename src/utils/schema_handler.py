import functools
import json
import os

from jsonschema import RefResolver, ValidationError, validate

from constants import SCHEMA_PATH
from errors import InvalidSchema


class SchemaCache:
    """Guarda os schemas já lidos do disco, pra não reler a cada requisição.

    Um schema é um arquivo .json que não muda enquanto a API está no ar.
    Ler o disco a cada POST seria trabalho repetido — então a primeira
    requisição lê e guarda, e as próximas pegam daqui.

    O dicionário é de CLASSE, não de instância: existe um só, e ninguém
    precisa passar o cache adiante.
    """

    schemas = {}

    @staticmethod
    def get_schema(schema_file_name: str) -> dict:
        schema_path = os.path.join(SCHEMA_PATH, schema_file_name)

        if schema_path in SchemaCache.schemas:
            return SchemaCache.schemas[schema_path]

        if not os.path.isfile(schema_path):
            raise Exception(
                f"Nao encontrei o schema '{schema_file_name}' em {SCHEMA_PATH}. "
                + "Ou o arquivo nao existe, ou o nome escrito na rota esta diferente."
            )

        with open(schema_path, "r", encoding="utf-8") as arquivo:
            schema = json.loads(arquivo.read())

        SchemaCache.schemas[schema_path] = schema

        return schema


class SchemaHandler:
    """Confere o corpo da requisição contra um arquivo de schema.

    ────────────────────────────────────────────────────────────────
    POR QUE JSON SCHEMA, E NÃO O PYDANTIC
    ────────────────────────────────────────────────────────────────
    O FastAPI já sabe validar sozinho: bastava escrever uma classe que
    herda de BaseModel e pedi-la na assinatura da rota. Foi assim que
    este projeto começou, e funcionava.

    A troca não é técnica, é de vocabulário. Nos serviços da QI Tech o
    contrato de entrada é um arquivo .json escrito em JSON Schema — um
    padrão que existe fora do Python e que o time inteiro lê, inclusive
    quem integra com a API e nunca vai abrir este repositório. Um
    projeto de estudo que ensina o jeito do framework ensina uma coisa
    a mais pra desaprender depois.

    Repare no que se ganha de quebra: o contrato virou um arquivo que
    se lê sozinho. Quem vai integrar com esta API precisa saber o que
    mandar no corpo — e agora recebe o .json, sem precisar abrir o
    repositório nem saber Python.

    ────────────────────────────────────────────────────────────────
    O QUE ISSO CUSTA
    ────────────────────────────────────────────────────────────────
    O corpo chega como um dicionário cru, e não como um objeto com
    campos. Onde antes se escrevia `payload.hello`, agora se escreve
    `payload["hello"]` — e o editor não completa mais o nome do campo
    nem avisa se você digitar errado. O contrato saiu do código e foi
    morar num arquivo à parte: melhor pra quem integra, um pouco pior
    pra quem digita.
    """

    @staticmethod
    def validate(schema_file_name: str):
        """Decorator que valida o `payload` antes de o resource rodar.

        Usa-se assim, no método do resource que recebe corpo:

            class PixTransactionResource:
                @SchemaHandler.validate("post_pix_transaction.json")
                def on_post(self, payload: dict) -> dict:

        O nome do arquivo é o único argumento, e ele aponta pra dentro
        de src/schemas/. Método que não recebe corpo — um GET, um
        DELETE — não leva decorator nenhum: não há o que conferir.

        Repare que o endereço HTTP não aparece aqui. Quem liga
        "/pix/transaction" a este método é o src/app.py, e é de propósito:
        o resource cuida do CONTEÚDO da requisição, o app.py cuida do
        ENDEREÇO dela.
        """

        def decorator_validate(func):
            @functools.wraps(func)
            def wrapper_validate(*args, **kwargs):
                if "payload" not in kwargs:
                    raise Exception(
                        f"A rota '{func.__name__}' foi decorada com o SchemaHandler, mas nao "
                        + "tem um parametro chamado 'payload'. E de la que sai o corpo a validar."
                    )

                schema = SchemaCache.get_schema(schema_file_name)
                resolver = RefResolver(f"file://{SCHEMA_PATH}/", None)

                try:
                    validate(kwargs["payload"], schema, resolver=resolver)
                except ValidationError as error:
                    raise InvalidSchema(describe_schema_error(error))

                return func(*args, **kwargs)

            return wrapper_validate

        return decorator_validate

    @staticmethod
    def validate_query_params(schema_file_name: str):
        """Decorator que valida a QUERY STRING antes de o resource rodar.

        Usa-se assim, num metodo que recebe a requisicao inteira:

            class PixTransactionResource:
                @SchemaHandler.validate_query_params("get_sample_entities.json")
                def on_get_list(self, request: Request) -> JSONResponse:

        Por que ler do `request` em vez dos argumentos da funcao: o
        FastAPI so entrega o que ele mesmo declarou. Um parametro com o
        nome errado — `?stauts=pending` — nunca chegaria aqui, e passaria
        batido como passa hoje em qualquer API que so declara o que
        conhece. Lendo a query string crua, o `additionalProperties:
        false` do schema pega o engano e responde dizendo o nome errado.

        O segundo detalhe e a LISTA. Na query string, `?status=a&status=b`
        e o mesmo campo repetido, e nada no texto diz se `?status=a`
        sozinho era pra ser lista de um ou valor unico. Quem sabe disso e
        o schema: o campo declarado como "type": "array" vem por
        `getlist`, o resto vem simples.
        """

        def decorator_validate(func):
            @functools.wraps(func)
            def wrapper_validate(*args, **kwargs):
                request = kwargs.get("request")
                if request is None:
                    raise Exception(
                        f"A rota '{func.__name__}' foi decorada com o validate_query_params, mas "
                        + "nao tem um parametro chamado 'request'. E de la que sai a query string."
                    )

                schema = SchemaCache.get_schema(schema_file_name)
                resolver = RefResolver(f"file://{SCHEMA_PATH}/", None)
                query_params = query_params_to_dict(request.query_params, schema)

                try:
                    validate(query_params, schema, resolver=resolver)
                except ValidationError as error:
                    raise InvalidSchema(describe_schema_error(error))

                return func(*args, **kwargs)

            return wrapper_validate

        return decorator_validate


def query_params_to_dict(query_params, schema: dict) -> dict:
    """A query string virada dicionario, com o schema dizendo o que e lista.

    Campo ausente nao entra: quem nao veio nao tem o que validar, e um
    None no lugar faria o schema reclamar de tipo por um filtro que a
    pessoa simplesmente nao usou.
    """
    properties = schema.get("properties", {})
    parsed_params = {}

    for param_name in query_params.keys():
        declared = properties.get(param_name, {})

        if declared.get("type") == "array":
            parsed_params[param_name] = query_params.getlist(param_name)
        else:
            parsed_params[param_name] = query_params[param_name]

    return parsed_params


def describe_schema_error(error: ValidationError) -> str:
    """Diz o que estava errado e ONDE, quando o campo é aninhado.

    O jsonschema já escreve uma boa mensagem ("'hello' is a required
    property"), mas ela não diz em que parte do JSON o problema está.
    Para um campo na raiz isso não faz falta; para um campo dentro de
    uma lista dentro de um objeto, faz toda.
    """
    location = []
    for part in error.absolute_path:
        location.append(str(part))

    if location:
        return f"{error.message} in {'.'.join(location)}"

    return error.message
