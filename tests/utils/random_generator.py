import random
from uuid import uuid4


class RandomGenerator:
    @staticmethod
    def generate_cpf():
        """Um CPF que existe, com máscara. A conta está aqui de novo, e não
        importada: o teste não enxerga o `src/`."""
        cpf = [random.randrange(10) for _ in range(9)]
        while len(set(cpf)) == 1:
            cpf = [random.randrange(10) for _ in range(9)]

        for _ in range(2):
            value = sum([(len(cpf) + 1 - i) * v for i, v in enumerate(cpf)]) % 11
            cpf.append(11 - value if value > 1 else 0)
        cpf_number = "".join(str(x) for x in cpf)
        cpf_number = f"{cpf_number[0:3]}.{cpf_number[3:6]}.{cpf_number[6:9]}-{cpf_number[9:11]}"
        return cpf_number

    @staticmethod
    def generate_cnpj():
        cnpj = [random.randrange(10) for _ in range(8)] + [0, 0, 0, 1]

        for _ in range(2):
            value = sum(v * (i % 8 + 2) for i, v in enumerate(reversed(cnpj)))
            digit = 11 - value % 11
            cnpj.append(digit if digit < 10 else 0)

        cnpj_number = "".join(str(x) for x in cnpj)

        cnpj_number = (
            f"{cnpj_number[0:2]}.{cnpj_number[2:5]}.{cnpj_number[5:8]}/{cnpj_number[8:12]}-{cnpj_number[12:14]}"
        )

        return cnpj_number

    @staticmethod
    def generate_digits(length: int) -> str:
        return "".join(str(random.randrange(10)) for _ in range(length))

    @staticmethod
    def generate_transaction_id() -> str:
        return f"pix-{uuid4().hex}"
