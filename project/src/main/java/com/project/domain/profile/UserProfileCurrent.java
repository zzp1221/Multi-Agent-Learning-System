package com.project.domain.profile;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/**
 * 用户的当前画像快照。
 */
@Entity
@Table(name = "user_profile_current", schema = "app")
public class UserProfileCurrent {

    @Id
    @Column(name = "user_id", nullable = false, updatable = false)
    private UUID userId;

    @Column(name = "active_snapshot_id")
    private UUID activeSnapshotId;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "profile_json", nullable = false)
    private Map<String, Object> profileJson = new LinkedHashMap<>();

    @Column(name = "summary_text", nullable = false)
    private String summaryText = "";

    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt = OffsetDateTime.now();

    public UUID getUserId() {
        return userId;
    }

    public void setUserId(UUID userId) {
        this.userId = userId;
    }

    public UUID getActiveSnapshotId() {
        return activeSnapshotId;
    }

    public void setActiveSnapshotId(UUID activeSnapshotId) {
        this.activeSnapshotId = activeSnapshotId;
    }

    public Map<String, Object> getProfileJson() {
        return profileJson;
    }

    public void setProfileJson(Map<String, Object> profileJson) {
        this.profileJson = profileJson;
    }

    public String getSummaryText() {
        return summaryText;
    }

    public void setSummaryText(String summaryText) {
        this.summaryText = summaryText;
    }

    public OffsetDateTime getUpdatedAt() {
        return updatedAt;
    }

    public void setUpdatedAt(OffsetDateTime updatedAt) {
        this.updatedAt = updatedAt;
    }
}
