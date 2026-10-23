"""模型注册点：导入全部 ORM 模型，确保 Base.metadata 完整（建表/迁移/测试用）。"""

from app.db import Base  # noqa: F401
from core.admin.models import (  # noqa: F401
    DomInstance,
    GovPermission,
    GovRole,
    GovRolePermission,
    GovUser,
)
from core.evaluation.models import EvalResult, EvalStandard  # noqa: F401
from core.gateway.models import GovAuditLog, GovTool  # noqa: F401
from core.growth.models import GrowthCorrectionRule, GrowthFeedback  # noqa: F401
from core.knowledge.models import (  # noqa: F401
    KbChunk,
    KbConflict,
    KbDocument,
    KbEntity,
    KbIngestTask,
    KbRelation,
)
from core.memory.models import MemEvent, MemItem  # noqa: F401
from core.orchestration.models import (  # noqa: F401
    AgentCheckpoint,
    ChatMessage,
    ChatSession,
    ChatSessionSummary,
)
from core.runs.models import (  # noqa: F401
    AgentRun,
    AgentRunCheckpoint,
    AgentRunEvent,
    AgentRunOutbox,
    OutputReview,
    RunPayload,
    RunPayloadSource,
)
from domains.chemical.models import (  # noqa: F401
    ChemCustomerNeed,
    ChemExperiment,
    ChemFormula,
    ChemProcess,
    ChemProduct,
    ChemSolution,
)
from domains.investment.models import (  # noqa: F401
    InvEnterprise,
    InvParkResource,
    InvPolicy,
    InvProfile,
)


def create_all(engine) -> None:
    Base.metadata.create_all(bind=engine)
