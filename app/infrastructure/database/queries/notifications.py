"""SQL запросы для уведомлений"""

GET_UNREAD_NOTIFICATIONS = """
SELECT
    notification_id,
    notification_type,
    title,
    message,
    severity,
    related_task_id,
    metadata,
    created_at
FROM wms.notifications
WHERE user_id  = $1
  AND is_read  = FALSE
ORDER BY
    CASE severity
        WHEN 'critical' THEN 1
        WHEN 'warning'  THEN 2
        ELSE 3
    END,
    created_at DESC;
"""

MARK_NOTIFICATION_READ = """
UPDATE wms.notifications
SET is_read = TRUE, read_at = NOW()
WHERE notification_id = $1
RETURNING notification_id, is_read;
"""

CREATE_NOTIFICATION = """
INSERT INTO wms.notifications (
    user_id, notification_type,
    title, message, severity,
    related_task_id, metadata
)
VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
RETURNING notification_id;
"""
