CPF_LENGTH = 11
CNPJ_LENGTH = 14

CPF_CHECK_DIGIT_POSITIONS = [9, 10]

CNPJ_FIRST_WEIGHTS = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
CNPJ_SECOND_WEIGHTS = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]


def only_digits(document_number: str) -> str:
    """O documento normalizado: a máscara sai, os dígitos ficam.

    É assim que o documento é gravado e comparado. "123.456.789-09" e
    "12345678909" são o mesmo pagador — sem isto, uma máscara diferente
    abriria um histórico novo e zeraria todas as regras de histórico.
    """
    digits = ""
    for character in document_number:
        if character.isdigit():
            digits = digits + character

    return digits


def is_valid_document(document_number: str) -> bool:
    """CPF (11 dígitos) ou CNPJ (14), conferindo os dígitos verificadores."""
    digits = only_digits(document_number)

    if len(digits) == CPF_LENGTH:
        return is_valid_cpf(digits)

    if len(digits) == CNPJ_LENGTH:
        return is_valid_cnpj(digits)

    return False


def _all_digits_are_equal(digits: list) -> bool:
    for digit in digits:
        if digit != digits[0]:
            return False

    return True


def is_valid_cpf(document_number: str) -> bool:
    """Diz se um CPF existe de verdade — não se ele tem a cara certa.

    O schema já cobrou o formato. Os dois últimos dígitos de um CPF são o
    RESULTADO de uma conta feita sobre os nove primeiros; por isso um
    número pode ter o formato perfeito e não existir — e a recusa é 422,
    não 400: a API leu, entendeu, e o valor não pode existir.

    A conta, para cada um dos dois dígitos: multiplica cada dígito
    anterior por um peso que decresce, soma tudo, tira o resto da divisão
    por 11. Resto menor que 2 vira dígito 0; nos outros casos, o dígito é
    11 menos o resto.
    """
    digits = []
    for character in only_digits(document_number):
        digits.append(int(character))

    if len(digits) != CPF_LENGTH:
        return False

    # Dígitos todos iguais passam na conta e mesmo assim não valem.
    if _all_digits_are_equal(digits):
        return False

    for position in CPF_CHECK_DIGIT_POSITIONS:
        total = 0
        first_weight = position + 1

        for index in range(position):
            total = total + digits[index] * (first_weight - index)

        remainder = total % 11

        if remainder < 2:
            expected_digit = 0
        else:
            expected_digit = 11 - remainder

        if digits[position] != expected_digit:
            return False

    return True


def is_valid_cnpj(document_number: str) -> bool:
    """A mesma ideia do CPF, com os pesos do CNPJ (5..2, 9..2 e 6..2, 9..2)."""
    digits = []
    for character in only_digits(document_number):
        digits.append(int(character))

    if len(digits) != CNPJ_LENGTH:
        return False

    if _all_digits_are_equal(digits):
        return False

    for weights in [CNPJ_FIRST_WEIGHTS, CNPJ_SECOND_WEIGHTS]:
        position = len(weights)
        total = 0

        for index in range(position):
            total = total + digits[index] * weights[index]

        remainder = total % 11

        if remainder < 2:
            expected_digit = 0
        else:
            expected_digit = 11 - remainder

        if digits[position] != expected_digit:
            return False

    return True


def document_type(document_number: str) -> str:
    """PF para CPF, PJ para CNPJ. Supõe o documento já normalizado."""
    if len(document_number) == CPF_LENGTH:
        return "PF"

    return "PJ"
