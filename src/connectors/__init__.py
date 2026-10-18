# O motor antifraude não chama nenhum serviço de fora (escopo do desafio):
# toda decisão sai do payload e do histórico próprio, em poucos
# milissegundos e sem depender de ninguém estar de pé. Por isso não há
# connector concreto aqui. O RestConnector fica como o andaime do Base
# Service, para o dia em que uma consulta externa entrar — e quem a
# chamaria seria o controller, nunca o resource.
