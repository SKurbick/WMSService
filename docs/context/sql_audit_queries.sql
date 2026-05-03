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
