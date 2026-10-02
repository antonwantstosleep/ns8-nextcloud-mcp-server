<!--
  Copyright (C) 2026 Anton
  SPDX-License-Identifier: GPL-3.0-or-later
-->
<template>
  <cv-grid fullWidth>
    <cv-row>
      <cv-column class="page-title">
        <h2>{{ $t("settings.title") }}</h2>
      </cv-column>
    </cv-row>
    <cv-row v-if="error.getConfiguration">
      <cv-column>
        <NsInlineNotification
          kind="error"
          :title="$t('action.get-configuration')"
          :description="error.getConfiguration"
          :showCloseButton="false"
        />
      </cv-column>
    </cv-row>
    <cv-row>
      <cv-column>
        <cv-tile light>
          <cv-form @submit.prevent="configureModule">
            <cv-text-input
              :label="$t('settings.host')"
              :placeholder="$t('settings.host_placeholder')"
              v-model.trim="host"
              class="mg-bottom"
              :invalid-message="error.host"
              :disabled="stillLoading"
              ref="host"
            >
            </cv-text-input>
            <NsToggle
              value="letsEncrypt"
              :label="core.$t('apps_lets_encrypt.request_https_certificate')"
              v-model="isLetsEncryptEnabled"
              :disabled="stillLoading"
              class="mg-bottom"
            >
              <template #tooltip>
                <div class="mg-bottom-sm">
                  {{ core.$t("apps_lets_encrypt.lets_encrypt_tips") }}
                </div>
                <div class="mg-bottom-sm">
                  <cv-link @click="goToCertificates">
                    {{ core.$t("apps_lets_encrypt.go_to_tls_certificates") }}
                  </cv-link>
                </div>
              </template>
              <template slot="text-left">{{
                $t("settings.disabled")
              }}</template>
              <template slot="text-right">{{
                $t("settings.enabled")
              }}</template>
            </NsToggle>
            <cv-row
              v-if="isLetsEncryptCurrentlyEnabled && !isLetsEncryptEnabled"
            >
              <cv-column>
                <NsInlineNotification
                  kind="warning"
                  :title="
                    core.$t('apps_lets_encrypt.lets_encrypt_disabled_warning')
                  "
                  :description="
                    core.$t(
                      'apps_lets_encrypt.lets_encrypt_disabled_warning_description',
                      {
                        node: this.status.node_ui_name
                          ? this.status.node_ui_name
                          : this.status.node,
                      }
                    )
                  "
                  :showCloseButton="false"
                />
              </cv-column>
            </cv-row>
            <cv-toggle
              value="httpToHttps"
              :label="$t('settings.http_to_https')"
              v-model="isHttpToHttpsEnabled"
              :disabled="stillLoading"
              class="mg-bottom"
            >
              <template slot="text-left">{{
                $t("settings.disabled")
              }}</template>
              <template slot="text-right">{{
                $t("settings.enabled")
              }}</template>
            </cv-toggle>
            <cv-text-input
              :label="$t('settings.nextcloud_host')"
              :placeholder="$t('settings.nextcloud_host_placeholder')"
              :helper-text="$t('settings.nextcloud_host_help')"
              v-model.trim="nextcloudHost"
              class="mg-bottom"
              :invalid-message="error.nextcloud_host"
              :disabled="stillLoading"
              ref="nextcloud_host"
            >
            </cv-text-input>
            <cv-text-input
              :label="$t('settings.nextcloud_username')"
              v-model.trim="nextcloudUsername"
              class="mg-bottom"
              :invalid-message="error.nextcloud_username"
              :disabled="stillLoading"
              ref="nextcloud_username"
            >
            </cv-text-input>
            <cv-text-input
              type="password"
              :label="$t('settings.nextcloud_password')"
              :helper-text="$t('settings.nextcloud_password_help')"
              v-model="nextcloudPassword"
              class="mg-bottom"
              :invalid-message="error.nextcloud_password"
              :disabled="stillLoading"
              ref="nextcloud_password"
            >
            </cv-text-input>
            <cv-toggle
              value="semanticSearch"
              :label="$t('settings.enable_semantic_search')"
              v-model="enableSemanticSearch"
              :disabled="stillLoading"
              class="mg-bottom"
            >
              <template slot="text-left">{{
                $t("settings.disabled")
              }}</template>
              <template slot="text-right">{{
                $t("settings.enabled")
              }}</template>
            </cv-toggle>
            <p class="helper mg-bottom">
              {{ $t("settings.enable_semantic_search_help") }}
            </p>
            <cv-text-input
              :label="$t('settings.ollama_base_url')"
              :placeholder="$t('settings.ollama_base_url_placeholder')"
              :helper-text="$t('settings.ollama_base_url_help')"
              v-model.trim="ollamaBaseUrl"
              class="mg-bottom"
              :invalid-message="error.ollama_base_url"
              :disabled="stillLoading"
              ref="ollama_base_url"
            >
            </cv-text-input>
            <cv-text-input
              :label="$t('settings.ollama_embedding_model')"
              :helper-text="$t('settings.ollama_embedding_model_help')"
              v-model.trim="ollamaEmbeddingModel"
              class="mg-bottom"
              :invalid-message="error.ollama_embedding_model"
              :disabled="stillLoading"
              ref="ollama_embedding_model"
            >
            </cv-text-input>
            <cv-row v-if="error.configureModule">
              <cv-column>
                <NsInlineNotification
                  kind="error"
                  :title="$t('action.configure-module')"
                  :description="error.configureModule"
                  :showCloseButton="false"
                />
              </cv-column>
            </cv-row>
            <cv-row v-if="error.getStatus">
              <cv-column>
                <NsInlineNotification
                  kind="error"
                  :title="$t('action.get-status')"
                  :description="error.getStatus"
                  :showCloseButton="false"
                />
              </cv-column>
            </cv-row>
            <cv-row v-if="validationErrorDetails.length">
              <cv-column>
                <NsInlineNotification
                  kind="error"
                  :title="
                    core.$t('apps_lets_encrypt.cannot_obtain_certificate')
                  "
                  :showCloseButton="false"
                >
                  <template #description>
                    <div class="flex flex-col gap-2">
                      <div
                        v-for="(detail, index) in validationErrorDetails"
                        :key="index"
                      >
                        {{ detail }}
                      </div>
                    </div>
                  </template>
                </NsInlineNotification>
              </cv-column>
            </cv-row>
            <NsButton
              kind="primary"
              :icon="Save20"
              :loading="loading.configureModule"
              :disabled="stillLoading"
              >{{ $t("settings.save") }}</NsButton
            >
          </cv-form>
        </cv-tile>
      </cv-column>
    </cv-row>
  </cv-grid>
</template>

<script>
import to from "await-to-js";
import { mapState } from "vuex";
import {
  QueryParamService,
  UtilService,
  TaskService,
  IconService,
  PageTitleService,
} from "@nethserver/ns8-ui-lib";

export default {
  name: "Settings",
  mixins: [
    TaskService,
    IconService,
    UtilService,
    QueryParamService,
    PageTitleService,
  ],
  pageTitle() {
    return this.$t("settings.title") + " - " + this.appName;
  },
  data() {
    return {
      q: {
        page: "settings",
      },
      status: {},
      validationErrorDetails: [],
      urlCheckInterval: null,
      host: "",
      isLetsEncryptEnabled: false,
      isLetsEncryptCurrentlyEnabled: false,
      isHttpToHttpsEnabled: true,
      nextcloudHost: "",
      nextcloudUsername: "",
      nextcloudPassword: "",
      ollamaBaseUrl: "",
      ollamaEmbeddingModel: "nomic-embed-text",
      enableSemanticSearch: true,
      loading: {
        getConfiguration: false,
        configureModule: false,
        getStatus: false,
        getDefaults: false,
      },
      error: {
        getConfiguration: "",
        configureModule: "",
        host: "",
        lets_encrypt: "",
        http2https: "",
        nextcloud_host: "",
        nextcloud_username: "",
        nextcloud_password: "",
        ollama_base_url: "",
        ollama_embedding_model: "",
        enable_semantic_search: "",
        getStatus: "",
        getDefaults: "",
      },
    };
  },
  computed: {
    ...mapState(["instanceName", "core", "appName"]),
    stillLoading() {
      return (
        this.loading.getConfiguration ||
        this.loading.configureModule ||
        this.loading.getStatus ||
        this.loading.getDefaults
      );
    },
  },
  created() {
    this.getConfiguration();
    this.getStatus();
    this.getDefaults();
  },
  beforeRouteEnter(to, from, next) {
    next((vm) => {
      vm.watchQueryData(vm);
      vm.urlCheckInterval = vm.initUrlBindingForApp(vm, vm.q.page);
    });
  },
  beforeRouteLeave(to, from, next) {
    clearInterval(this.urlCheckInterval);
    next();
  },
  methods: {
    goToCertificates() {
      this.core.$router.push("/settings/tls-certificates");
    },
    async getStatus() {
      this.loading.getStatus = true;
      this.error.getStatus = "";
      const taskAction = "get-status";
      const eventId = this.getUuid();
      this.core.$root.$once(
        `${taskAction}-aborted-${eventId}`,
        this.getStatusAborted
      );
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        this.getStatusCompleted
      );
      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: taskAction,
          extra: {
            title: this.$t("action." + taskAction),
            isNotificationHidden: true,
            eventId,
          },
        })
      );
      const err = res[0];
      if (err) {
        console.error(`error creating task ${taskAction}`, err);
        this.error.getStatus = this.getErrorMessage(err);
        this.loading.getStatus = false;
      }
    },
    getStatusAborted(taskResult, taskContext) {
      console.error(`${taskContext.action} aborted`, taskResult);
      this.error.getStatus = this.$t("error.generic_error");
      this.loading.getStatus = false;
    },
    getStatusCompleted(taskContext, taskResult) {
      this.status = taskResult.output;
      this.loading.getStatus = false;
    },
    async getConfiguration() {
      this.loading.getConfiguration = true;
      this.error.getConfiguration = "";
      const taskAction = "get-configuration";
      const eventId = this.getUuid();
      this.core.$root.$once(
        `${taskAction}-aborted-${eventId}`,
        this.getConfigurationAborted
      );
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        this.getConfigurationCompleted
      );
      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: taskAction,
          extra: {
            title: this.$t("action." + taskAction),
            isNotificationHidden: true,
            eventId,
          },
        })
      );
      const err = res[0];
      if (err) {
        console.error(`error creating task ${taskAction}`, err);
        this.error.getConfiguration = this.getErrorMessage(err);
        this.loading.getConfiguration = false;
      }
    },
    getConfigurationAborted(taskResult, taskContext) {
      console.error(`${taskContext.action} aborted`, taskResult);
      this.error.getConfiguration = this.$t("error.generic_error");
      this.loading.getConfiguration = false;
    },
    getConfigurationCompleted(taskContext, taskResult) {
      const config = taskResult.output;
      this.host = config.host;
      this.isLetsEncryptEnabled = config.lets_encrypt;
      this.isLetsEncryptCurrentlyEnabled = config.lets_encrypt;
      this.isHttpToHttpsEnabled = config.http2https;
      this.nextcloudHost = config.nextcloud_host;
      this.nextcloudUsername = config.nextcloud_username;
      this.nextcloudPassword = config.nextcloud_password;
      this.ollamaBaseUrl = config.ollama_base_url;
      this.ollamaEmbeddingModel =
        config.ollama_embedding_model || "nomic-embed-text";
      this.enableSemanticSearch = config.enable_semantic_search;
      this.loading.getConfiguration = false;
      this.focusElement("host");
    },
    validateConfigureModule() {
      this.clearErrors(this);
      this.validationErrorDetails = [];
      let isValidationOk = true;
      const required = [
        ["host", this.host],
        ["nextcloud_host", this.nextcloudHost],
        ["nextcloud_username", this.nextcloudUsername],
        ["nextcloud_password", this.nextcloudPassword],
        ["ollama_embedding_model", this.ollamaEmbeddingModel],
      ];
      if (this.enableSemanticSearch) {
        required.push(["ollama_base_url", this.ollamaBaseUrl]);
      }
      for (const [field, value] of required) {
        if (!value) {
          this.error[field] = this.$t("common.required");
          if (isValidationOk) {
            this.focusElement(field);
          }
          isValidationOk = false;
        }
      }
      return isValidationOk;
    },
    configureModuleValidationFailed(validationErrors) {
      this.loading.configureModule = false;
      let focusAlreadySet = false;
      for (const validationError of validationErrors) {
        const param = validationError.parameter;
        if (validationError.details) {
          this.validationErrorDetails = validationError.details
            .split("\n")
            .filter((detail) => detail.trim() !== "");
        } else {
          const key = "settings." + validationError.error;
          this.error[param] = this.$te(key)
            ? this.$t(key)
            : validationError.error;
          if (!focusAlreadySet) {
            this.focusElement(param);
            focusAlreadySet = true;
          }
        }
      }
    },
    async configureModule() {
      if (!this.validateConfigureModule()) {
        return;
      }
      this.loading.configureModule = true;
      const taskAction = "configure-module";
      const eventId = this.getUuid();
      this.core.$root.$once(
        `${taskAction}-aborted-${eventId}`,
        this.configureModuleAborted
      );
      this.core.$root.$once(
        `${taskAction}-validation-failed-${eventId}`,
        this.configureModuleValidationFailed
      );
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        this.configureModuleCompleted
      );
      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: taskAction,
          data: {
            host: this.host,
            lets_encrypt: this.isLetsEncryptEnabled,
            http2https: this.isHttpToHttpsEnabled,
            nextcloud_host: this.nextcloudHost,
            nextcloud_username: this.nextcloudUsername,
            nextcloud_password: this.nextcloudPassword,
            ollama_base_url: this.ollamaBaseUrl,
            ollama_embedding_model: this.ollamaEmbeddingModel,
            enable_semantic_search: this.enableSemanticSearch,
          },
          extra: {
            title: this.$t("settings.instance_configuration", {
              instance: this.instanceName,
            }),
            description: this.$t("settings.configuring"),
            eventId,
          },
        })
      );
      const err = res[0];
      if (err) {
        console.error(`error creating task ${taskAction}`, err);
        this.error.configureModule = this.getErrorMessage(err);
        this.loading.configureModule = false;
      }
    },
    configureModuleAborted(taskResult, taskContext) {
      console.error(`${taskContext.action} aborted`, taskResult);
      this.error.configureModule = this.$t("error.generic_error");
      this.loading.configureModule = false;
    },
    configureModuleCompleted() {
      this.loading.configureModule = false;
      this.getConfiguration();
    },
    async getDefaults() {
      this.loading.getDefaults = true;
      const taskAction = "get-defaults";
      const eventId = this.getUuid();
      this.core.$root.$once(
        `${taskAction}-aborted-${eventId}`,
        this.getDefaultsAborted
      );
      this.core.$root.$once(
        `${taskAction}-completed-${eventId}`,
        this.getDefaultsCompleted
      );
      const res = await to(
        this.createModuleTaskForApp(this.instanceName, {
          action: taskAction,
          extra: {
            title: this.$t("action." + taskAction),
            isNotificationHidden: true,
            eventId,
          },
        })
      );
      const err = res[0];
      if (err) {
        console.error(`error creating task ${taskAction}`, err);
        this.error.getDefaults = this.getErrorMessage(err);
        this.loading.getDefaults = false;
      }
    },
    getDefaultsAborted(taskResult, taskContext) {
      console.error(`${taskContext.action} aborted`, taskResult);
      this.error.getDefaults = this.$t("error.generic_error");
      this.loading.getDefaults = false;
    },
    getDefaultsCompleted(taskContext, taskResult) {
      const defaults = taskResult.output || {};
      if (!this.host && !this.loading.getConfiguration) {
        this.isHttpToHttpsEnabled = defaults.http2https;
        this.isLetsEncryptEnabled = defaults.lets_encrypt;
        this.enableSemanticSearch = defaults.enable_semantic_search;
        if (!this.ollamaEmbeddingModel) {
          this.ollamaEmbeddingModel = defaults.ollama_embedding_model;
        }
      }
      this.loading.getDefaults = false;
    },
  },
};
</script>

<style scoped lang="scss">
@import "../styles/carbon-utils";
.mg-bottom {
  margin-bottom: $spacing-06;
}
.helper {
  max-width: 38rem;
  color: $text-02;
}
</style>
