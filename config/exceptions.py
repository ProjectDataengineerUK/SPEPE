class SPEPEError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class BudgetExceededError(SPEPEError):
    pass


class MCPConnectionError(SPEPEError):
    def __init__(self, message: str, platform: str = "unknown"):
        self.platform = platform
        super().__init__(message)


class DataNotFoundError(SPEPEError):
    def __init__(self, uf: str, ano: int):
        self.uf = uf
        self.ano = ano
        super().__init__(
            f"Dados não encontrados para {uf}/{ano}. Execute /coletar {uf} {ano} primeiro."
        )


class IBGEAPIError(SPEPEError):
    pass


class ModelFitError(SPEPEError):
    pass


class DisclaimerMissingError(SPEPEError):
    """Raised when a forecast output lacks the mandatory disclaimer block."""

    def __init__(self, agent_name: str | None = None):
        self.agent_name = agent_name
        msg = (
            f"Output de previsão do agente '{agent_name}' não contém o disclaimer obrigatório. "
            "Adicione: '⚠️ Esta projeção é baseada em dados históricos e modelos estatísticos. "
            "Não constitui certeza eleitoral. Margem de erro: ±Xpp (IC 95%).'"
        )
        super().__init__(msg)
