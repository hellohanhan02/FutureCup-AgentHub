"""Workspace records on the explicitly selected application database, never a hardcoded history DB."""
import json
from futurecup.domain.errors import RuntimeFault


class SQLiteWorkspaceRepository:
    def __init__(self, workflows):self.workflows=workflows

    async def open(self):
        await self.workflows.start()
        self.workflows.repository.db.execute('CREATE TABLE IF NOT EXISTS workspace_tasks(id TEXT PRIMARY KEY,body TEXT NOT NULL)')
        self.workflows.repository.db.commit()

    async def save(self, record):
        await self.open()
        with self.workflows.repository.db:
            self.workflows.repository.db.execute('INSERT INTO workspace_tasks VALUES(?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',
                (record['id'],json.dumps(record,ensure_ascii=False,allow_nan=False)))

    async def get(self, identifier):
        await self.open()
        row=self.workflows.repository.db.execute('SELECT body FROM workspace_tasks WHERE id=?',(identifier,)).fetchone()
        if row is None:raise RuntimeFault('workspace_not_found','Task not found',404)
        return json.loads(row[0])

    async def list(self):
        await self.open()
        return [json.loads(r[0]) for r in self.workflows.repository.db.execute('SELECT body FROM workspace_tasks ORDER BY rowid DESC LIMIT 100')]
