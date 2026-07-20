-- 1. Некорректные movements quantity
SELECT COUNT(*) AS bad_quantity_count
FROM wms.movements
WHERE quantity IS NULL OR quantity <= 0;

-- 2. Movements без направления
SELECT COUNT(*) AS no_side_count
FROM wms.movements
WHERE from_location_id IS NULL
  AND to_location_id IS NULL;

-- 3. Movements с несуществующим container_code
SELECT COUNT(*) AS orphan_container_code_count
FROM wms.movements m
LEFT JOIN wms.containers c ON c.qr_code = m.container_code
WHERE m.container_code IS NOT NULL
  AND c.container_id IS NULL;

-- 4. Inventory с несуществующим container_code
SELECT COUNT(*) AS orphan_inventory_container_code_count
FROM wms.inventory i
LEFT JOIN wms.containers c ON c.qr_code = i.container_code
WHERE i.container_code IS NOT NULL
  AND c.container_id IS NULL;

-- 5. FBS items с movement_id без movement
SELECT COUNT(*) AS orphan_fbs_movement_count
FROM wms.fbs_shipment_items f
LEFT JOIN wms.movements m ON m.movement_id = f.movement_id
WHERE f.movement_id IS NOT NULL
  AND m.movement_id IS NULL;


-- 6. Kit movements без source-связи
SELECT COUNT(*) AS bad_kit_movement_source_count
FROM wms.movements
WHERE movement_type IN ('kit_assembly', 'kit_disassembly')
  AND (
      source_type IS DISTINCT FROM 'kit_operation'
      OR source_id IS NULL
      OR source_item_id IS NULL
  );

-- 7. Kit movements с source_id/source_item_id без связанной operation/item
SELECT COUNT(*) AS orphan_kit_movement_source_count
FROM wms.movements m
LEFT JOIN wms.kit_operations ko
       ON ko.operation_id = m.source_id
LEFT JOIN wms.kit_operation_items koi
       ON koi.item_id = m.source_item_id
      AND koi.operation_id = ko.operation_id
WHERE m.source_type = 'kit_operation'
  AND m.movement_type IN ('kit_assembly', 'kit_disassembly')
  AND (
      ko.operation_id IS NULL
      OR koi.item_id IS NULL
  );

-- 8. Kit operation items с movement_id без movement
SELECT COUNT(*) AS orphan_kit_item_movement_count
FROM wms.kit_operation_items koi
LEFT JOIN wms.movements m
       ON m.movement_id = koi.movement_id
WHERE koi.movement_id IS NOT NULL
  AND m.movement_id IS NULL;

-- 9. Completed kit operation items без movement_id
SELECT COUNT(*) AS completed_kit_items_without_movement_count
FROM wms.kit_operation_items koi
JOIN wms.kit_operations ko
     ON ko.operation_id = koi.operation_id
WHERE ko.status = 'completed'
  AND koi.movement_id IS NULL;

-- 10. Active operation_locations указывают на inactive location
SELECT COUNT(*) AS active_operation_locations_with_inactive_location_count
FROM wms.operation_locations ol
JOIN wms.locations l
     ON l.location_id = ol.location_id
WHERE ol.is_active = true
  AND l.is_active = false;

-- 11. Дубли operation_locations по operation_code/location_id/scope
-- В норме должно быть 0, unique index должен это запрещать.
SELECT COUNT(*) AS duplicate_operation_locations_count
FROM (
    SELECT operation_code, location_id, scope
    FROM wms.operation_locations
    GROUP BY operation_code, location_id, scope
    HAVING COUNT(*) > 1
) d;

-- 12. Kit operation items с невалидной direction-логикой movement
SELECT COUNT(*) AS bad_kit_item_movement_direction_count
FROM wms.kit_operation_items koi
JOIN wms.kit_operations ko
     ON ko.operation_id = koi.operation_id
JOIN wms.movements m
     ON m.movement_id = koi.movement_id
WHERE
    (
        koi.role = 'component_consumption'
        AND (
            m.movement_type <> 'kit_assembly'
            OR m.from_location_id IS NULL
            OR m.to_location_id IS NOT NULL
        )
    )
    OR
    (
        koi.role = 'kit_result'
        AND (
            m.movement_type <> 'kit_assembly'
            OR m.from_location_id IS NOT NULL
            OR m.to_location_id IS NULL
        )
    )
    OR
    (
        koi.role = 'kit_consumption'
        AND (
            m.movement_type <> 'kit_disassembly'
            OR m.from_location_id IS NULL
            OR m.to_location_id IS NOT NULL
        )
    )
    OR
    (
        koi.role = 'component_result'
        AND (
            m.movement_type <> 'kit_disassembly'
            OR m.from_location_id IS NOT NULL
            OR m.to_location_id IS NULL
        )
    );
-- Re-sorting: completed operations must have exactly two items and both roles.
SELECT o.operation_id, count(i.item_id) AS item_count
FROM wms.re_sorting_operations o LEFT JOIN wms.re_sorting_operation_items i USING(operation_id)
WHERE o.status='completed' GROUP BY o.operation_id HAVING count(i.item_id) <> 2;

SELECT o.operation_id
FROM wms.re_sorting_operations o LEFT JOIN wms.re_sorting_operation_items i USING(operation_id)
WHERE o.status='completed' GROUP BY o.operation_id
HAVING count(*) FILTER (WHERE i.role='source_outgoing') <> 1
    OR count(*) FILTER (WHERE i.role='target_incoming') <> 1;

-- Items without a movement identity or whose partitioned movement is absent.
SELECT * FROM wms.re_sorting_operation_items WHERE movement_id IS NULL OR movement_created_at IS NULL;
SELECT i.* FROM wms.re_sorting_operation_items i
LEFT JOIN wms.movements m ON m.movement_id=i.movement_id AND m.created_at=i.movement_created_at
WHERE i.movement_id IS NOT NULL AND m.movement_id IS NULL;

-- Movement source linkage must match operation/item.
SELECT i.item_id,m.movement_id FROM wms.re_sorting_operation_items i
JOIN wms.movements m ON m.movement_id=i.movement_id AND m.created_at=i.movement_created_at
WHERE m.source_type <> 're_sorting_operation' OR m.source_id <> i.operation_id
   OR m.source_item_id <> i.item_id OR m.movement_type <> 're_sorting';

-- The two directed deltas must net to zero and quantities must agree.
SELECT i.operation_id, sum(CASE WHEN i.role='source_outgoing' THEN -m.quantity ELSE m.quantity END) AS net_delta
FROM wms.re_sorting_operation_items i JOIN wms.movements m
 ON m.movement_id=i.movement_id AND m.created_at=i.movement_created_at
GROUP BY i.operation_id HAVING sum(CASE WHEN i.role='source_outgoing' THEN -m.quantity ELSE m.quantity END) <> 0;
SELECT operation_id,min(quantity),max(quantity) FROM wms.re_sorting_operation_items
GROUP BY operation_id HAVING min(quantity) <> max(quantity);

-- Active re-sorting permissions may not reference inactive locations.
SELECT ol.* FROM wms.operation_locations ol JOIN wms.locations l USING(location_id)
WHERE ol.operation_code='re_sorting_operations' AND ol.scope='direct'
  AND ol.is_active=TRUE AND l.is_active=FALSE;
