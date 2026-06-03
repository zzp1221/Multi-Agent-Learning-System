package com.project.domain.task;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class ServiceTypeTest {

    @Test
    void fromValueAcceptsPersonalizedLearningCaseInsensitively() {
        assertThat(ServiceType.fromValue("personalized_learning")).isEqualTo(ServiceType.PERSONALIZED_LEARNING);
        assertThat(ServiceType.PERSONALIZED_LEARNING.value()).isEqualTo("PERSONALIZED_LEARNING");
    }

    @Test
    void fromValueRejectsUnsupportedServiceType() {
        assertThatThrownBy(() -> ServiceType.fromValue("personalized-learning"))
            .isInstanceOf(IllegalArgumentException.class)
            .hasMessageContaining("Unsupported serviceType");
    }
}
