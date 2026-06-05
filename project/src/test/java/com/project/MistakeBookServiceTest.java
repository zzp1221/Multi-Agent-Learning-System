package com.project;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.project.application.learningpath.PersonalizedLearningRefreshService;
import com.project.application.mistake.MistakeBookService;
import com.project.application.mistake.MistakeBookService.MistakeSchedule;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

class MistakeBookServiceTest {

    private final MistakeBookService service = new MistakeBookService(
        null,
        new ObjectMapper(),
        mock(PersonalizedLearningRefreshService.class)
    );
    private final OffsetDateTime now = OffsetDateTime.parse("2026-05-17T12:00:00+08:00");

    @Test
    void lowQualityResetsReviewToTomorrow() {
        MistakeSchedule schedule = service.calculateNextSchedule(3, new BigDecimal("2.50"), 12, 2, now);

        assertThat(schedule.intervalDays()).isEqualTo(1);
        assertThat(schedule.nextReviewAt()).isEqualTo(now.plusDays(1));
        assertThat(schedule.mastered()).isFalse();
        assertThat(schedule.easeFactor()).isEqualByComparingTo("2.18");
    }

    @Test
    void firstSuccessfulReviewSchedulesOneDay() {
        MistakeSchedule schedule = service.calculateNextSchedule(0, new BigDecimal("2.50"), 1, 4, now);

        assertThat(schedule.intervalDays()).isEqualTo(1);
        assertThat(schedule.nextReviewAt()).isEqualTo(now.plusDays(1));
        assertThat(schedule.mastered()).isFalse();
    }

    @Test
    void secondSuccessfulReviewSchedulesSixDays() {
        MistakeSchedule schedule = service.calculateNextSchedule(1, new BigDecimal("2.50"), 1, 5, now);

        assertThat(schedule.intervalDays()).isEqualTo(6);
        assertThat(schedule.nextReviewAt()).isEqualTo(now.plusDays(6));
        assertThat(schedule.mastered()).isFalse();
    }

    @Test
    void matureSuccessfulReviewCanMarkMastered() {
        MistakeSchedule schedule = service.calculateNextSchedule(4, new BigDecimal("2.50"), 9, 5, now);

        assertThat(schedule.intervalDays()).isEqualTo(23);
        assertThat(schedule.nextReviewAt()).isEqualTo(now.plusDays(23));
        assertThat(schedule.mastered()).isTrue();
    }

    @Test
    void easeFactorHasLowerBound() {
        MistakeSchedule schedule = service.calculateNextSchedule(4, new BigDecimal("1.31"), 8, 0, now);

        assertThat(schedule.easeFactor()).isEqualByComparingTo("1.30");
        assertThat(schedule.intervalDays()).isEqualTo(1);
        assertThat(schedule.mastered()).isFalse();
    }
}
