package com.project.config;

import com.project.infrastructure.ratelimit.IpRateLimitFilter;
import com.project.infrastructure.ratelimit.RateLimitFilter;
import com.project.security.InternalTokenAuthenticationFilter;
import com.project.security.JwtAuthenticationFilter;
import com.project.security.RestAuthenticationEntryPoint;
import jakarta.servlet.DispatcherType;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.config.Customizer;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;

/**
 * Java 控制平面的安全配置。
 *
 * <p>设计上保持应用无状态，以 JWT Bearer 令牌为核心。
 * 公开端点严格限定于健康检查、API 文档和认证入口；
 * 所有业务 API 默认受保护。</p>
 */
@Configuration
public class SecurityConfiguration {

    @Bean
    public SecurityFilterChain securityFilterChain(
        HttpSecurity http,
        IpRateLimitFilter ipRateLimitFilter,
        InternalTokenAuthenticationFilter internalTokenAuthenticationFilter,
        JwtAuthenticationFilter jwtAuthenticationFilter,
        RestAuthenticationEntryPoint authenticationEntryPoint,
        RateLimitFilter rateLimitFilter
    ) throws Exception {
        return http
            .csrf(csrf -> csrf.disable())
            .cors(Customizer.withDefaults())
            .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
            .exceptionHandling(exceptions -> exceptions.authenticationEntryPoint(authenticationEntryPoint))
            .authorizeHttpRequests(authorize -> authorize
                .dispatcherTypeMatchers(DispatcherType.ASYNC, DispatcherType.ERROR).permitAll()
                .requestMatchers(
                    "/actuator/health",
                    "/actuator/info",
                    "/api/health",
                    "/error",
                    "/api-docs/**",
                    "/swagger-ui.html",
                    "/swagger-ui/**",
                    "/api/auth/register",
                    "/api/auth/login",
                    "/api/voice/ws",
                    "/api/conversations/images/*"
                )
                .permitAll()
                .requestMatchers("/internal/**")
                .hasAuthority("INTERNAL_SERVICE")
                .anyRequest()
                .authenticated()
            )
            .addFilterBefore(ipRateLimitFilter, UsernamePasswordAuthenticationFilter.class)
            .addFilterAfter(internalTokenAuthenticationFilter, IpRateLimitFilter.class)
            .addFilterAfter(jwtAuthenticationFilter, InternalTokenAuthenticationFilter.class)
            .addFilterAfter(rateLimitFilter, JwtAuthenticationFilter.class)
            .build();
    }

    @Bean
    public PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder();
    }
}
