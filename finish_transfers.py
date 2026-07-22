"""Run remaining pending transfers."""
import sys
sys.path.insert(0, ".")

from app.models import init_db, get_session
from app.models.task import SyncTask
from app.services.transfer_coordinator import TransferCoordinator
from app.services.principal_mapper import PrincipalMapper
from app.config import AppConfig, AnyShareConfig, BishengConfig, SyncConfig, SchedulerConfig
from sqlmodel import select

init_db()

config = AppConfig(
    anyshare=AnyShareConfig(base_url="https://5j-zsgl.powerchina.cn", client_id="t", client_secret="t"),
    bisheng=BishengConfig(base_url="http://192.168.106.161:7860", cookie_value="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ7XCJ1c2VyX2lkXCI6IDEsIFwidXNlcl9uYW1lXCI6IFwiYWRtaW5cIiwgXCJ0ZW5hbnRfaWRcIjogMSwgXCJ0b2tlbl92ZXJzaW9uXCI6IDF9IiwiZXhwIjoxNzg0MTg0MTgyLCJpc3MiOiJiaXNoZW5nIn0.P3strwbOLHEtKG_TS72MGOW-Lm8vCNsaJ5MuJtq9Csg"),
    sync=SyncConfig(),
    scheduler=SchedulerConfig(),
)

AS_TOKEN = "ory_at_-AkDvJN0mz43LsrQNJDvbupUWH8Xvom5Q5Yj1Vt7Hzk.rF7WqC3uTezoA7Ur4UasLYP05WFPwI3rMND3cKndQ5s"

with get_session() as s:
    tasks = s.exec(select(SyncTask).where(SyncTask.status == "pending")).all()
    print(f"Remaining: {len(tasks)}")

mapper = PrincipalMapper()
coordinator = TransferCoordinator(config, mapper, token=AS_TOKEN)

for task in tasks:
    print(f"Transferring task {task.id}...")
    result = coordinator.transfer_one(task)
    print(f"  {result.get('status', 'unknown')}")

print("Done")
