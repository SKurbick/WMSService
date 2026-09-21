"""KIZ identity operations; never create a physical movement."""
import asyncpg
from app.core.exceptions import LocationNotFoundError, ProductNotFoundError
from app.core.kiz_errors import KizConflictError, KizNotFoundError
from app.core.schemas.kiz import KizAssignment, KizTerminalRequest


class KizService:
    def __init__(self, repository):
        self.repo = repository

    async def _scope(self, conn, product_id, location_code):
        if not await self.repo.get_product(conn, product_id):
            raise ProductNotFoundError(f'Товар {product_id} не найден')
        location = await self.repo.get_location(conn, location_code)
        if not location:
            raise LocationNotFoundError(f'Локация {location_code} не найдена')
        return location

    async def _get(self, conn, kiz_code, *, lock=False):
        row = await self.repo.get(conn, kiz_code, lock=lock)
        if row is None:
            raise KizNotFoundError(f'КИЗ {kiz_code} не найден')
        return dict(row)

    async def assign(self, data: KizAssignment):
        async with self.repo.pool.acquire() as conn:
            async with conn.transaction(isolation='read_committed'):
                return await self.assign_in_transaction(conn, data)

    async def assign_in_transaction(self, conn, data: KizAssignment):
        """Caller owns transaction; RR is safe due to touch before count."""
        if not conn.is_in_transaction():
            raise RuntimeError('KIZ assignment requires an open transaction')
        location = await self._scope(conn, data.product_id, data.location_code)
        if await self.repo.get(conn, data.kiz_code):
            raise KizConflictError('КИЗ уже существует')
        inventory = await self.repo.lock_inventory(conn, data.product_id, location['location_id'])
        if inventory is None or inventory['quantity'] <= 0:
            raise KizConflictError('Физический остаток для назначения КИЗ не найден')
        # Create a row version BEFORE count, also for caller-owned RR transactions.
        await self.repo.touch_inventory(conn, inventory['inventory_id'])
        identified = await self.repo.count_active(conn, data.product_id, location['location_id'])
        if inventory['quantity'] - identified < 1:
            raise KizConflictError('Нет целой неидентифицированной единицы товара')
        try:
            await self.repo.insert(conn, data, location['location_id'])
        except asyncpg.UniqueViolationError as exc:
            if exc.constraint_name != 'uq_kiz_code':
                raise
            raise KizConflictError('КИЗ уже существует') from exc
        kiz = await self._get(conn, data.kiz_code)
        await self.repo.event(conn, kiz, 'assigned', None, 'active', data)
        summary = await self.repo.summary(conn, data.product_id, location['location_id'])
        return dict(kiz=kiz, **summary)

    async def terminate(self, kiz_code, status, data: KizTerminalRequest):
        async with self.repo.pool.acquire() as conn:
            async with conn.transaction(isolation='read_committed'):
                return await self.terminate_in_transaction(conn, kiz_code, status, data)

    async def terminate_in_transaction(self, conn, kiz_code, status, data):
        if not conn.is_in_transaction():
            raise RuntimeError('KIZ transition requires an open transaction')
        if status not in {'error', 'deactivated'}:
            raise KizConflictError('Недопустимый переход КИЗ')
        initial = await self._get(conn, kiz_code)
        if initial.get('container_id') is None:
            await self.repo.lock_inventory(conn, initial['product_id'], initial['location_id'])
        else:
            await self.repo.lock_container_inventory(
                conn, initial['product_id'], initial['container_id']
            )
        kiz = await self._get(conn, kiz_code, lock=True)
        if kiz['lifecycle_status'] != 'active':
            raise KizConflictError('КИЗ уже закрыт')
        if (kiz['product_id'], kiz['location_id'], kiz.get('container_id')) != (
            initial['product_id'], initial['location_id'], initial.get('container_id')
        ):
            raise KizConflictError('КИЗ изменился до terminal transition')
        await self.repo.terminate(conn, kiz['kiz_id'], status)
        event = 'marked_as_error' if status == 'error' else 'deactivated'
        await self.repo.event(conn, kiz, event, 'active', status, data)
        return await self._get(conn, kiz_code)

    async def get(self, kiz_code):
        async with self.repo.pool.acquire() as conn:
            return await self._get(conn, kiz_code)

    async def list(self, product_id=None, location_code=None, lifecycle_status=None,
                   container_id=None, container_qr_code=None, limit=50, offset=0):
        async with self.repo.pool.acquire() as conn:
            async with conn.transaction(isolation='repeatable_read', readonly=True):
                return await self.repo.list(
                    conn, product_id, location_code, lifecycle_status,
                    container_id, container_qr_code, limit, offset)

    async def events(self, kiz_code, limit=50, offset=0):
        async with self.repo.pool.acquire() as conn:
            async with conn.transaction(isolation='repeatable_read', readonly=True):
                kiz = await self._get(conn, kiz_code)
                return await self.repo.events(conn, kiz['kiz_id'], limit, offset)

    async def summary(self, product_id, location_code=None, container_id=None,
                      container_qr_code=None):
        scopes = int(location_code is not None) + int(container_id is not None) + int(container_qr_code is not None)
        if scopes != 1:
            raise KizConflictError(
                'Укажите ровно один holder scope: location_code, container_id или container_qr_code'
            )
        async with self.repo.pool.acquire() as conn:
            async with conn.transaction(isolation='repeatable_read', readonly=True):
                if location_code is not None:
                    location = await self._scope(conn, product_id, location_code)
                    return await self.repo.summary(conn, product_id, location['location_id'])
                if not await self.repo.get_product(conn, product_id):
                    raise ProductNotFoundError(f'Товар {product_id} не найден')
                container = await self.repo.get_container(conn, container_id, container_qr_code)
                if container is None:
                    raise KizConflictError('Контейнер не найден')
                return await self.repo.container_summary(conn, product_id, container['container_id'])

    async def integrity(self):
        async with self.repo.pool.acquire() as conn:
            return await self.repo.integrity(conn)
