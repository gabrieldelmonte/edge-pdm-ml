/*
 * setup.c - WiFi, captive portal, NVS config storage, and hardware init.
 *
 * Config is persisted as JSON in NVS namespace "edge_cfg" key "state_json"
 * with a SPIFFS fallback at /spiffs/app_state.json.
 *
 * Schema migration: on load, if "schema_version" key is absent (v0),
 * defaults are applied for missing fields and schema_version=1 is written back.
 */
#include "setup.h"
#include "../../include/app_constants.h"

#include <string.h>
#include <stdio.h>
#include <stdlib.h>

#include "esp_log.h"
#include "esp_err.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "esp_spiffs.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_http_server.h"
#include "cJSON.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

static const char *TAG = "setup";

/* Semaphore given by POST handler to unblock app_captive_portal_start(). */
static SemaphoreHandle_t s_portal_done_sem = NULL;

/* -------------------------------------------------------------------------
 * Forward declarations of static helpers
 * ------------------------------------------------------------------------- */
static esp_err_t config_to_json(const config_t *cfg, char **out_json);
static esp_err_t json_to_config(const char *json, config_t *cfg);
static void      apply_config_defaults(config_t *cfg);
static esp_err_t save_to_nvs(const char *json);
static esp_err_t save_to_spiffs(const char *json);
static esp_err_t load_from_nvs(char **out_json);
static esp_err_t load_from_spiffs(char **out_json);
static esp_err_t portal_get_handler(httpd_req_t *req);
static esp_err_t portal_post_handler(httpd_req_t *req);

/* -------------------------------------------------------------------------
 * Captive portal HTML
 * The form is a single C string literal.  Sections are separated by
 * comment blocks for clarity.
 * ------------------------------------------------------------------------- */
static const char PORTAL_HTML[] =
"<!DOCTYPE html>"
"<html lang=\"en\">"
"<head>"
"<meta charset=\"UTF-8\">"
"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
"<title>Edge PDM Configuration</title>"
"<style>"
"body{font-family:sans-serif;max-width:600px;margin:20px auto;padding:0 12px;background:#f4f4f4;}"
"h1{color:#333;font-size:1.3em;}"
"h2{color:#555;font-size:1.1em;border-bottom:1px solid #ccc;padding-bottom:4px;margin-top:20px;}"
"label{display:block;margin-top:10px;font-weight:bold;font-size:0.9em;color:#444;}"
"input[type=text],input[type=password],input[type=number],select{"
"  width:100%;box-sizing:border-box;padding:7px;margin-top:3px;"
"  border:1px solid #bbb;border-radius:4px;font-size:0.9em;}"
".row{display:flex;gap:8px;}"
".row>div{flex:1;}"
"input[type=submit]{width:100%;padding:10px;background:#0078d4;color:#fff;"
"  border:none;border-radius:4px;font-size:1em;cursor:pointer;margin-top:18px;}"
"input[type=submit]:hover{background:#005fa3;}"
".section{background:#fff;padding:14px;border-radius:6px;margin-bottom:10px;"
"  box-shadow:0 1px 3px rgba(0,0,0,0.1);}"
".hidden{display:none;}"
".radio-group label{display:inline;font-weight:normal;margin-right:12px;}"
".radio-group input{width:auto;margin-right:4px;}"
"input[type=checkbox]{width:auto;margin-right:6px;}"
".check-label{font-weight:normal;display:inline;}"
"</style>"
"</head>"
"<body>"
"<h1>Edge PDM Device Configuration</h1>"
"<form method=\"POST\" action=\"/save\" id=\"cfg-form\">"

/* ---- Network ---- */
"<div class=\"section\">"
"<h2>Network</h2>"
"<label>WiFi SSID</label>"
"<input type=\"text\" name=\"wifi_ssid\" required maxlength=\"63\">"
"<label>WiFi Password</label>"
"<input type=\"password\" name=\"wifi_password\" maxlength=\"63\">"
"</div>"

/* ---- Identity ---- */
"<div class=\"section\">"
"<h2>Device</h2>"
"<label>Device Name</label>"
"<input type=\"text\" name=\"device_name\" value=\"edge-sensor-01\" maxlength=\"63\">"
"</div>"

/* ---- Sampling ---- */
"<div class=\"section\">"
"<h2>Sampling</h2>"
"<div class=\"row\">"
"<div><label>Sampling Duration (ms)</label>"
"<input type=\"number\" name=\"sampling_time_ms\" value=\"5000\" min=\"100\" max=\"60000\"></div>"
"<div><label>Deep Sleep Duration (s)</label>"
"<input type=\"number\" name=\"deep_sleep_duration_s\" value=\"60\" min=\"1\" max=\"3600\"></div>"
"</div>"
"</div>"

/* ---- Timezone ---- */
"<div class=\"section\">"
"<h2>Timezone</h2>"
"<label>TZ String (POSIX)</label>"
"<input type=\"text\" name=\"timezone\" value=\"UTC0\" maxlength=\"31\">"
"</div>"

/* ---- Transport ---- */
"<div class=\"section\">"
"<h2>Transport</h2>"
"<div class=\"radio-group\">"
"<label><input type=\"radio\" name=\"transport_mode\" value=\"HTTPS\" checked onchange=\"onTransport(this)\">HTTPS</label>"
"<label><input type=\"radio\" name=\"transport_mode\" value=\"MQTT\" onchange=\"onTransport(this)\">MQTT</label>"
"</div>"
"<div id=\"https-opts\">"
"<label>Middleware Host</label>"
"<input type=\"text\" name=\"middleware_host\" value=\"192.168.1.200\" maxlength=\"127\">"
"<div><label>Middleware Port</label>"
"<input type=\"number\" name=\"middleware_port\" value=\"8080\" min=\"1\" max=\"65535\"></div>"
"</div>"
"<div id=\"mqtt-opts\" class=\"hidden\">"
"<label>MQTT Broker Host</label>"
"<input type=\"text\" name=\"mqtt_host\" value=\"192.168.1.100\" maxlength=\"127\">"
"<div class=\"row\">"
"<div><label>MQTT Port</label>"
"<input type=\"number\" name=\"mqtt_port\" value=\"1883\" min=\"1\" max=\"65535\"></div>"
"<div><label>Batch Size (rows)</label>"
"<input type=\"number\" name=\"mqtt_batch_size\" value=\"64\" min=\"1\" max=\"1024\"></div>"
"</div>"
"</div>"
"</div>"

/* ---- Accelerometers ---- */
"<div class=\"section\">"
"<h2>Accelerometers</h2>"
"<label>SPI Bus Pins</label>"
"<div class=\"row\">"
"<div><label>MOSI Pin</label><input type=\"number\" name=\"lis3dh_mosi_pin\" value=\"11\" min=\"0\" max=\"48\"></div>"
"<div><label>MISO Pin</label><input type=\"number\" name=\"lis3dh_miso_pin\" value=\"13\" min=\"0\" max=\"48\"></div>"
"<div><label>SCLK Pin</label><input type=\"number\" name=\"lis3dh_sclk_pin\" value=\"12\" min=\"0\" max=\"48\"></div>"
"</div>"
"<p style=\"margin-top:14px\"><input type=\"checkbox\" name=\"lis3dh_enabled\" id=\"lis3dh_enabled\" checked onchange=\"onLIS3DH(this)\">"
"<label class=\"check-label\" for=\"lis3dh_enabled\">LIS3DH</label></p>"
"<div id=\"lis3dh-opts\">"
"<div class=\"row\">"
"<div><label>ODR</label>"
"<select name=\"lis3dh_odr\">"
"<option value=\"0\">100 Hz</option>"
"<option value=\"1\">200 Hz</option>"
"<option value=\"2\" selected>400 Hz</option>"
"<option value=\"3\">1344 Hz</option>"
"<option value=\"4\">1620 Hz LP</option>"
"<option value=\"5\">5376 Hz LP</option>"
"</select></div>"
"<div><label>Full Scale</label>"
"<select name=\"lis3dh_full_scale\">"
"<option value=\"0\">+/- 2g</option>"
"<option value=\"1\">+/- 4g</option>"
"<option value=\"2\">+/- 8g</option>"
"<option value=\"3\">+/- 16g</option>"
"</select></div>"
"</div>"
"<div><label>CS Pin</label><input type=\"number\" name=\"lis3dh_cs_pin\" value=\"10\" min=\"0\" max=\"48\"></div>"
"</div>"

"<p style=\"margin-top:14px\">"
"<input type=\"checkbox\" name=\"adxl345_enabled\" id=\"adxl345_enabled\" checked onchange=\"onADXL(this)\">"
"<label class=\"check-label\" for=\"adxl345_enabled\">ADXL345</label></p>"
"<div id=\"adxl345-opts\">"
"<div class=\"row\">"
"<div><label>Data Rate</label>"
"<select name=\"adxl345_data_rate\">"
"<option value=\"0\">800 Hz</option>"
"<option value=\"1\">1600 Hz</option>"
"<option value=\"2\">3200 Hz</option>"
"</select></div>"
"<div><label>CS Pin</label><input type=\"number\" name=\"adxl345_cs_pin\" value=\"7\" min=\"0\" max=\"48\"></div>"
"</div>"
"</div>"
"</div>"

/* ---- Optional peripherals ---- */
"<div class=\"section\">"
"<h2>Optional Peripherals</h2>"
"<p><input type=\"checkbox\" name=\"oled_enabled\" id=\"oled_enabled\" onchange=\"onOLED(this)\">"
"<label class=\"check-label\" for=\"oled_enabled\">OLED Display (SSD1306)</label></p>"
"<div id=\"oled-opts\" class=\"hidden\">"
"<div class=\"row\">"
"<div><label>I2C SDA Pin</label><input type=\"number\" name=\"oled_sda_pin\" value=\"21\" min=\"0\" max=\"48\"></div>"
"<div><label>I2C SCL Pin</label><input type=\"number\" name=\"oled_scl_pin\" value=\"22\" min=\"0\" max=\"48\"></div>"
"</div>"
"</div>"
"<p style=\"margin-top:10px\">"
"<input type=\"checkbox\" name=\"ina219_enabled\" id=\"ina219_enabled\" onchange=\"onINA219(this)\">"
"<label class=\"check-label\" for=\"ina219_enabled\">INA219 Current Monitor</label></p>"
"<div id=\"ina219-opts\" class=\"hidden\">"
"<div class=\"row\">"
"<div><label>I2C SDA Pin</label><input type=\"number\" name=\"ina219_sda_pin\" value=\"21\" min=\"0\" max=\"48\"></div>"
"<div><label>I2C SCL Pin</label><input type=\"number\" name=\"ina219_scl_pin\" value=\"22\" min=\"0\" max=\"48\"></div>"
"</div>"
"</div>"
"</div>"

"<input type=\"submit\" value=\"Save Configuration\">"
"</form>"

"<script>"
"function onTransport(el){"
"  document.getElementById('https-opts').classList.toggle('hidden',el.value!=='HTTPS');"
"  document.getElementById('mqtt-opts').classList.toggle('hidden',el.value!=='MQTT');"
"}"
"function onLIS3DH(el){"
"  document.getElementById('lis3dh-opts').classList.toggle('hidden',!el.checked);"
"}"
"function onADXL(el){"
"  document.getElementById('adxl345-opts').classList.toggle('hidden',!el.checked);"
"}"
"function onOLED(el){"
"  document.getElementById('oled-opts').classList.toggle('hidden',!el.checked);"
"}"
"function onINA219(el){"
"  document.getElementById('ina219-opts').classList.toggle('hidden',!el.checked);"
"}"
"document.getElementById('cfg-form').addEventListener('submit',function(e){"
"  var l=document.getElementById('lis3dh_enabled').checked;"
"  var a=document.getElementById('adxl345_enabled').checked;"
"  if(!l&&!a){alert('Select at least one accelerometer');e.preventDefault();}"
"});"
"</script>"
"</body></html>";

/* -------------------------------------------------------------------------
 * apply_config_defaults
 * ------------------------------------------------------------------------- */
static void apply_config_defaults(config_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    strncpy(cfg->device_name,       "edge-sensor-01",        sizeof(cfg->device_name) - 1);
    strncpy(cfg->middleware_host,   "192.168.1.200",         sizeof(cfg->middleware_host) - 1);
    cfg->middleware_port            = 8080;
    cfg->sampling_time_ms           = 5000;
    cfg->deep_sleep_duration_s      = 60;
    strncpy(cfg->timezone,          "UTC0",                  sizeof(cfg->timezone) - 1);
    cfg->transport_mode             = TRANSPORT_HTTPS;
    strncpy(cfg->mqtt_host,         APP_MQTT_DEFAULT_HOST,   sizeof(cfg->mqtt_host) - 1);
    cfg->mqtt_port                  = APP_MQTT_DEFAULT_PORT;
    cfg->mqtt_batch_size            = APP_MQTT_DEFAULT_BATCH_SIZE;

    cfg->lis3dh.enabled     = true;
    cfg->lis3dh.odr         = LIS3DH_ODR_400HZ;
    cfg->lis3dh.full_scale  = LIS3DH_FS_2G;
    cfg->lis3dh.cs_pin      = APP_LIS3DH_DEFAULT_CS_PIN;
    cfg->lis3dh.mosi_pin    = APP_LIS3DH_DEFAULT_MOSI_PIN;
    cfg->lis3dh.miso_pin    = APP_LIS3DH_DEFAULT_MISO_PIN;
    cfg->lis3dh.sclk_pin    = APP_LIS3DH_DEFAULT_SCLK_PIN;

    cfg->adxl345.enabled    = true;
    cfg->adxl345.data_rate  = ADXL345_RATE_800HZ;
    cfg->adxl345.cs_pin     = APP_ADXL345_DEFAULT_CS_PIN;

    cfg->oled.enabled       = false;
    cfg->oled.sda_pin       = APP_DEFAULT_I2C_SDA_PIN;
    cfg->oled.scl_pin       = APP_DEFAULT_I2C_SCL_PIN;

    cfg->ina219.enabled     = false;
    cfg->ina219.sda_pin     = APP_DEFAULT_I2C_SDA_PIN;
    cfg->ina219.scl_pin     = APP_DEFAULT_I2C_SCL_PIN;

    cfg->schema_version     = APP_CONFIG_SCHEMA_VERSION;
}

/* -------------------------------------------------------------------------
 * config_to_json
 * ------------------------------------------------------------------------- */
static esp_err_t config_to_json(const config_t *cfg, char **out_json)
{
    cJSON *root = cJSON_CreateObject();
    if (!root) {
        ESP_LOGE(TAG, "config_to_json: cJSON_CreateObject failed");
        return ESP_ERR_NO_MEM;
    }

    cJSON_AddNumberToObject(root, "schema_version",         cfg->schema_version);
    cJSON_AddStringToObject(root, "wifi_ssid",              cfg->wifi_ssid);
    cJSON_AddStringToObject(root, "wifi_password",          cfg->wifi_password);
    cJSON_AddStringToObject(root, "device_name",            cfg->device_name);
    cJSON_AddStringToObject(root, "middleware_host",        cfg->middleware_host);
    cJSON_AddNumberToObject(root, "middleware_port",        cfg->middleware_port);
    cJSON_AddNumberToObject(root, "sampling_time_ms",       cfg->sampling_time_ms);
    cJSON_AddNumberToObject(root, "deep_sleep_duration_s",  cfg->deep_sleep_duration_s);
    cJSON_AddStringToObject(root, "timezone",               cfg->timezone);
    cJSON_AddNumberToObject(root, "transport_mode",         (int)cfg->transport_mode);
    cJSON_AddStringToObject(root, "mqtt_host",              cfg->mqtt_host);
    cJSON_AddNumberToObject(root, "mqtt_port",              cfg->mqtt_port);
    cJSON_AddNumberToObject(root, "mqtt_batch_size",        cfg->mqtt_batch_size);

    /* LIS3DH */
    cJSON_AddBoolToObject  (root, "lis3dh_enabled",         cfg->lis3dh.enabled);
    cJSON_AddNumberToObject(root, "lis3dh_odr",             (int)cfg->lis3dh.odr);
    cJSON_AddNumberToObject(root, "lis3dh_full_scale",      (int)cfg->lis3dh.full_scale);
    cJSON_AddNumberToObject(root, "lis3dh_cs_pin",          cfg->lis3dh.cs_pin);
    cJSON_AddNumberToObject(root, "lis3dh_mosi_pin",        cfg->lis3dh.mosi_pin);
    cJSON_AddNumberToObject(root, "lis3dh_miso_pin",        cfg->lis3dh.miso_pin);
    cJSON_AddNumberToObject(root, "lis3dh_sclk_pin",        cfg->lis3dh.sclk_pin);

    /* ADXL345 */
    cJSON_AddBoolToObject  (root, "adxl345_enabled",        cfg->adxl345.enabled);
    cJSON_AddNumberToObject(root, "adxl345_data_rate",      (int)cfg->adxl345.data_rate);
    cJSON_AddNumberToObject(root, "adxl345_cs_pin",         cfg->adxl345.cs_pin);

    /* OLED */
    cJSON_AddBoolToObject  (root, "oled_enabled",           cfg->oled.enabled);
    cJSON_AddNumberToObject(root, "oled_sda_pin",           cfg->oled.sda_pin);
    cJSON_AddNumberToObject(root, "oled_scl_pin",           cfg->oled.scl_pin);

    /* INA219 */
    cJSON_AddBoolToObject  (root, "ina219_enabled",         cfg->ina219.enabled);
    cJSON_AddNumberToObject(root, "ina219_sda_pin",         cfg->ina219.sda_pin);
    cJSON_AddNumberToObject(root, "ina219_scl_pin",         cfg->ina219.scl_pin);

    *out_json = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);

    if (!*out_json) {
        ESP_LOGE(TAG, "config_to_json: cJSON_Print failed");
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

/* -------------------------------------------------------------------------
 * json_to_config - parse JSON into cfg; migrate v0 -> v1 if needed
 * ------------------------------------------------------------------------- */
static esp_err_t json_to_config(const char *json, config_t *cfg)
{
    cJSON *root = cJSON_Parse(json);
    if (!root) {
        ESP_LOGE(TAG, "json_to_config: parse error");
        return ESP_FAIL;
    }

    /* Start with defaults so missing keys get sane values (migration). */
    apply_config_defaults(cfg);

#define GET_STR(key, field) do { \
    cJSON *_j = cJSON_GetObjectItem(root, key); \
    if (_j && cJSON_IsString(_j)) { \
        strncpy(cfg->field, _j->valuestring, sizeof(cfg->field) - 1); \
    } \
} while (0)

#define GET_INT(key, field) do { \
    cJSON *_j = cJSON_GetObjectItem(root, key); \
    if (_j && cJSON_IsNumber(_j)) { cfg->field = (typeof(cfg->field))_j->valuedouble; } \
} while (0)

#define GET_BOOL(key, field) do { \
    cJSON *_j = cJSON_GetObjectItem(root, key); \
    if (_j) { cfg->field = cJSON_IsTrue(_j); } \
} while (0)

    GET_INT  ("schema_version",        schema_version);
    GET_STR  ("wifi_ssid",             wifi_ssid);
    GET_STR  ("wifi_password",         wifi_password);
    GET_STR  ("device_name",           device_name);
    GET_STR  ("middleware_host",       middleware_host);
    GET_INT  ("middleware_port",       middleware_port);
    GET_INT  ("sampling_time_ms",      sampling_time_ms);
    GET_INT  ("deep_sleep_duration_s", deep_sleep_duration_s);
    GET_STR  ("timezone",              timezone);
    GET_INT  ("transport_mode",        transport_mode);
    GET_STR  ("mqtt_host",             mqtt_host);
    GET_INT  ("mqtt_port",             mqtt_port);
    GET_INT  ("mqtt_batch_size",       mqtt_batch_size);

    GET_BOOL ("lis3dh_enabled",        lis3dh.enabled);
    GET_INT  ("lis3dh_odr",            lis3dh.odr);
    GET_INT  ("lis3dh_full_scale",     lis3dh.full_scale);
    GET_INT  ("lis3dh_cs_pin",         lis3dh.cs_pin);
    GET_INT  ("lis3dh_mosi_pin",       lis3dh.mosi_pin);
    GET_INT  ("lis3dh_miso_pin",       lis3dh.miso_pin);
    GET_INT  ("lis3dh_sclk_pin",       lis3dh.sclk_pin);

    GET_BOOL ("adxl345_enabled",       adxl345.enabled);
    GET_INT  ("adxl345_data_rate",     adxl345.data_rate);
    GET_INT  ("adxl345_cs_pin",        adxl345.cs_pin);

    GET_BOOL ("oled_enabled",          oled.enabled);
    GET_INT  ("oled_sda_pin",          oled.sda_pin);
    GET_INT  ("oled_scl_pin",          oled.scl_pin);

    GET_BOOL ("ina219_enabled",        ina219.enabled);
    GET_INT  ("ina219_sda_pin",        ina219.sda_pin);
    GET_INT  ("ina219_scl_pin",        ina219.scl_pin);

#undef GET_STR
#undef GET_INT
#undef GET_BOOL

    cJSON_Delete(root);
    return ESP_OK;
}

/* -------------------------------------------------------------------------
 * NVS helpers
 * ------------------------------------------------------------------------- */
static esp_err_t save_to_nvs(const char *json)
{
    nvs_handle_t handle;
    esp_err_t err = nvs_open(APP_NVS_NAMESPACE, NVS_READWRITE, &handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "save_to_nvs: nvs_open failed: %s", esp_err_to_name(err));
        return err;
    }
    err = nvs_set_str(handle, APP_NVS_KEY, json);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "save_to_nvs: nvs_set_str failed: %s", esp_err_to_name(err));
    } else {
        err = nvs_commit(handle);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "save_to_nvs: nvs_commit failed: %s", esp_err_to_name(err));
        }
    }
    nvs_close(handle);
    return err;
}

static esp_err_t load_from_nvs(char **out_json)
{
    nvs_handle_t handle;
    esp_err_t err = nvs_open(APP_NVS_NAMESPACE, NVS_READONLY, &handle);
    if (err != ESP_OK) {
        return err;
    }

    size_t len = 0;
    err = nvs_get_str(handle, APP_NVS_KEY, NULL, &len);
    if (err != ESP_OK) {
        nvs_close(handle);
        return err;
    }

    *out_json = malloc(len);
    if (!*out_json) {
        nvs_close(handle);
        return ESP_ERR_NO_MEM;
    }

    err = nvs_get_str(handle, APP_NVS_KEY, *out_json, &len);
    nvs_close(handle);
    if (err != ESP_OK) {
        free(*out_json);
        *out_json = NULL;
        ESP_LOGE(TAG, "load_from_nvs: nvs_get_str failed: %s", esp_err_to_name(err));
    }
    return err;
}

/* -------------------------------------------------------------------------
 * SPIFFS helpers
 * ------------------------------------------------------------------------- */
static esp_err_t save_to_spiffs(const char *json)
{
    FILE *f = fopen(APP_SPIFFS_FALLBACK_PATH, "w");
    if (!f) {
        ESP_LOGE(TAG, "save_to_spiffs: cannot open %s for writing", APP_SPIFFS_FALLBACK_PATH);
        return ESP_FAIL;
    }
    fputs(json, f);
    fclose(f);
    return ESP_OK;
}

static esp_err_t load_from_spiffs(char **out_json)
{
    FILE *f = fopen(APP_SPIFFS_FALLBACK_PATH, "r");
    if (!f) {
        return ESP_FAIL;
    }
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);

    if (size <= 0) {
        fclose(f);
        return ESP_FAIL;
    }

    *out_json = malloc((size_t)size + 1);
    if (!*out_json) {
        fclose(f);
        return ESP_ERR_NO_MEM;
    }

    fread(*out_json, 1, (size_t)size, f);
    (*out_json)[size] = '\0';
    fclose(f);
    return ESP_OK;
}

/* -------------------------------------------------------------------------
 * Public: app_setup_init
 * ------------------------------------------------------------------------- */
esp_err_t app_setup_init(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "NVS needs erase, erasing...");
        nvs_flash_erase();
        err = nvs_flash_init();
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nvs_flash_init failed: %s", esp_err_to_name(err));
        return err;
    }

    esp_vfs_spiffs_conf_t spiffs_conf = {
        .base_path              = "/spiffs",
        .partition_label        = NULL,
        .max_files              = 8,
        .format_if_mount_failed = true,
    };
    err = esp_vfs_spiffs_register(&spiffs_conf);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "SPIFFS mount failed: %s", esp_err_to_name(err));
        return err;
    }

    ESP_LOGI(TAG, "NVS and SPIFFS initialized");
    return ESP_OK;
}

/* -------------------------------------------------------------------------
 * Public: app_config_defaults
 * ------------------------------------------------------------------------- */
void app_config_defaults(config_t *cfg)
{
    apply_config_defaults(cfg);
}

/* -------------------------------------------------------------------------
 * Public: app_config_load
 * ------------------------------------------------------------------------- */
esp_err_t app_config_load(config_t *cfg)
{
    char *json = NULL;

    /* Try NVS first, fall back to SPIFFS. */
    if (load_from_nvs(&json) != ESP_OK || !json) {
        ESP_LOGW(TAG, "NVS config not found, trying SPIFFS fallback");
        if (load_from_spiffs(&json) != ESP_OK || !json) {
            ESP_LOGW(TAG, "No saved config found, applying defaults");
            apply_config_defaults(cfg);
            return ESP_ERR_NOT_FOUND;
        }
    }

    esp_err_t err = json_to_config(json, cfg);
    free(json);

    if (err != ESP_OK) {
        ESP_LOGE(TAG, "app_config_load: json_to_config failed");
        apply_config_defaults(cfg);
        return err;
    }

    /* Schema migration: if version is below current, add missing fields
     * (already done in json_to_config via defaults) and save back. */
    if (cfg->schema_version < APP_CONFIG_SCHEMA_VERSION) {
        ESP_LOGI(TAG, "Migrating config v%d -> v%d",
                 cfg->schema_version, APP_CONFIG_SCHEMA_VERSION);
        cfg->schema_version = APP_CONFIG_SCHEMA_VERSION;
        app_config_save(cfg);
    }

    return ESP_OK;
}

/* -------------------------------------------------------------------------
 * Public: app_config_save
 * ------------------------------------------------------------------------- */
esp_err_t app_config_save(const config_t *cfg)
{
    char *json = NULL;
    esp_err_t err = config_to_json(cfg, &json);
    if (err != ESP_OK) {
        return err;
    }

    err = save_to_nvs(json);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "NVS save failed, writing SPIFFS fallback only");
    }
    /* Always attempt SPIFFS, even if NVS succeeded. */
    save_to_spiffs(json);
    free(json);
    return err;
}

/* -------------------------------------------------------------------------
 * HTTP captive portal handlers
 * ------------------------------------------------------------------------- */

/* Small utility: extract a URL-decoded field value from a POST body. */
static void get_post_field(const char *body, const char *key,
                           char *out, size_t out_len)
{
    out[0] = '\0';
    size_t klen = strlen(key);
    const char *p = body;
    while (*p) {
        if (strncmp(p, key, klen) == 0 && p[klen] == '=') {
            p += klen + 1;
            size_t i = 0;
            while (*p && *p != '&' && i < out_len - 1) {
                if (*p == '+') {
                    out[i++] = ' ';
                    p++;
                } else if (*p == '%' && p[1] && p[2]) {
                    char hex[3] = {p[1], p[2], '\0'};
                    out[i++] = (char)strtol(hex, NULL, 16);
                    p += 3;
                } else {
                    out[i++] = *p++;
                }
            }
            out[i] = '\0';
            return;
        }
        /* Skip to next field. */
        while (*p && *p != '&') {
            p++;
        }
        if (*p == '&') {
            p++;
        }
    }
}

/* Check if a field name appears in the POST body (for checkboxes). */
static bool field_present(const char *body, const char *key)
{
    char tmp[4];
    get_post_field(body, key, tmp, sizeof(tmp));
    /* Checkbox submits "on" when checked, absent when unchecked. */
    return tmp[0] != '\0';
}

static esp_err_t portal_get_handler(httpd_req_t *req)
{
    httpd_resp_set_type(req, "text/html");
    httpd_resp_send(req, PORTAL_HTML, HTTPD_RESP_USE_STRLEN);
    return ESP_OK;
}

static esp_err_t portal_post_handler(httpd_req_t *req)
{
    /* Read POST body. */
    int total = req->content_len;
    if (total <= 0 || total > 4096) {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Bad content length");
        return ESP_FAIL;
    }

    char *body = malloc((size_t)total + 1);
    if (!body) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "OOM");
        return ESP_FAIL;
    }

    int received = 0;
    while (received < total) {
        int ret = httpd_req_recv(req, body + received, (size_t)(total - received));
        if (ret <= 0) {
            free(body);
            httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Recv error");
            return ESP_FAIL;
        }
        received += ret;
    }
    body[total] = '\0';

    /* Validation: at least one accelerometer must be enabled. */
    bool lis3dh_en  = field_present(body, "lis3dh_enabled");
    bool adxl345_en = field_present(body, "adxl345_enabled");
    if (!lis3dh_en && !adxl345_en) {
        free(body);
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST,
                            "At least one accelerometer must be enabled");
        return ESP_FAIL;
    }

    /* Build config from POST fields. */
    config_t *cfg = req->user_ctx;
    apply_config_defaults(cfg);

    char tmp[128];

    get_post_field(body, "wifi_ssid",             cfg->wifi_ssid,       sizeof(cfg->wifi_ssid));
    get_post_field(body, "wifi_password",          cfg->wifi_password,   sizeof(cfg->wifi_password));
    get_post_field(body, "device_name",            cfg->device_name,     sizeof(cfg->device_name));
    get_post_field(body, "middleware_host",        cfg->middleware_host, sizeof(cfg->middleware_host));

    get_post_field(body, "middleware_port",        tmp, sizeof(tmp));
    if (tmp[0]) cfg->middleware_port = (uint16_t)atoi(tmp);

    get_post_field(body, "sampling_time_ms",       tmp, sizeof(tmp));
    if (tmp[0]) cfg->sampling_time_ms = (uint32_t)atoi(tmp);

    get_post_field(body, "deep_sleep_duration_s",  tmp, sizeof(tmp));
    if (tmp[0]) cfg->deep_sleep_duration_s = (uint32_t)atoi(tmp);

    get_post_field(body, "timezone",               cfg->timezone, sizeof(cfg->timezone));

    /* Transport */
    get_post_field(body, "transport_mode",         tmp, sizeof(tmp));
    cfg->transport_mode = (strcmp(tmp, "MQTT") == 0) ? TRANSPORT_MQTT : TRANSPORT_HTTPS;

    get_post_field(body, "mqtt_host",              cfg->mqtt_host, sizeof(cfg->mqtt_host));
    get_post_field(body, "mqtt_port",              tmp, sizeof(tmp));
    if (tmp[0]) cfg->mqtt_port = (uint16_t)atoi(tmp);
    get_post_field(body, "mqtt_batch_size",        tmp, sizeof(tmp));
    if (tmp[0]) cfg->mqtt_batch_size = (uint32_t)atoi(tmp);

    /* LIS3DH */
    cfg->lis3dh.enabled = lis3dh_en;
    get_post_field(body, "lis3dh_odr",             tmp, sizeof(tmp));
    if (tmp[0]) cfg->lis3dh.odr = (lis3dh_odr_t)atoi(tmp);
    get_post_field(body, "lis3dh_full_scale",      tmp, sizeof(tmp));
    if (tmp[0]) cfg->lis3dh.full_scale = (lis3dh_fs_t)atoi(tmp);
    get_post_field(body, "lis3dh_cs_pin",          tmp, sizeof(tmp));
    if (tmp[0]) cfg->lis3dh.cs_pin = atoi(tmp);
    get_post_field(body, "lis3dh_mosi_pin",        tmp, sizeof(tmp));
    if (tmp[0]) cfg->lis3dh.mosi_pin = atoi(tmp);
    get_post_field(body, "lis3dh_miso_pin",        tmp, sizeof(tmp));
    if (tmp[0]) cfg->lis3dh.miso_pin = atoi(tmp);
    get_post_field(body, "lis3dh_sclk_pin",        tmp, sizeof(tmp));
    if (tmp[0]) cfg->lis3dh.sclk_pin = atoi(tmp);

    /* ADXL345 */
    cfg->adxl345.enabled = adxl345_en;
    get_post_field(body, "adxl345_data_rate",      tmp, sizeof(tmp));
    if (tmp[0]) cfg->adxl345.data_rate = (adxl345_rate_t)atoi(tmp);
    get_post_field(body, "adxl345_cs_pin",         tmp, sizeof(tmp));
    if (tmp[0]) cfg->adxl345.cs_pin = atoi(tmp);

    /* OLED */
    cfg->oled.enabled = field_present(body, "oled_enabled");
    get_post_field(body, "oled_sda_pin",           tmp, sizeof(tmp));
    if (tmp[0]) cfg->oled.sda_pin = atoi(tmp);
    get_post_field(body, "oled_scl_pin",           tmp, sizeof(tmp));
    if (tmp[0]) cfg->oled.scl_pin = atoi(tmp);

    /* INA219 */
    cfg->ina219.enabled = field_present(body, "ina219_enabled");
    get_post_field(body, "ina219_sda_pin",         tmp, sizeof(tmp));
    if (tmp[0]) cfg->ina219.sda_pin = atoi(tmp);
    get_post_field(body, "ina219_scl_pin",         tmp, sizeof(tmp));
    if (tmp[0]) cfg->ina219.scl_pin = atoi(tmp);

    cfg->schema_version = APP_CONFIG_SCHEMA_VERSION;

    free(body);

    esp_err_t err = app_config_save(cfg);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "portal_post_handler: app_config_save failed");
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Save failed");
        return ESP_FAIL;
    }

    const char *resp =
        "<html><body style=\"font-family:sans-serif;max-width:500px;margin:40px auto\">"
        "<h2>Configuration Saved</h2>"
        "<p>The device will now reboot and connect to your WiFi network.</p>"
        "</body></html>";
    httpd_resp_set_type(req, "text/html");
    httpd_resp_send(req, resp, HTTPD_RESP_USE_STRLEN);

    /* Signal app_captive_portal_start() to exit its wait loop. */
    if (s_portal_done_sem) {
        xSemaphoreGive(s_portal_done_sem);
    }

    return ESP_OK;
}

/* -------------------------------------------------------------------------
 * Public: app_captive_portal_start
 * ------------------------------------------------------------------------- */
esp_err_t app_captive_portal_start(config_t *cfg)
{
    ESP_LOGI(TAG, "Starting captive portal AP");

    s_portal_done_sem = xSemaphoreCreateBinary();
    if (!s_portal_done_sem) {
        ESP_LOGE(TAG, "app_captive_portal_start: semaphore alloc failed");
        return ESP_ERR_NO_MEM;
    }

    esp_netif_create_default_wifi_ap();
    wifi_init_config_t wifi_init_cfg = WIFI_INIT_CONFIG_DEFAULT();
    esp_wifi_init(&wifi_init_cfg);

    wifi_config_t ap_cfg = {
        .ap = {
            .ssid            = "EdgePDM-Setup",
            .ssid_len        = 0,
            .password        = "",
            .channel         = 1,
            .authmode        = WIFI_AUTH_OPEN,
            .max_connection  = 4,
        },
    };
    esp_wifi_set_mode(WIFI_MODE_AP);
    esp_wifi_set_config(WIFI_IF_AP, &ap_cfg);
    esp_wifi_start();

    httpd_config_t httpd_cfg = HTTPD_DEFAULT_CONFIG();
    httpd_handle_t server    = NULL;
    if (httpd_start(&server, &httpd_cfg) != ESP_OK) {
        ESP_LOGE(TAG, "Failed to start HTTP server");
        return ESP_FAIL;
    }

    httpd_uri_t get_uri = {
        .uri      = "/",
        .method   = HTTP_GET,
        .handler  = portal_get_handler,
        .user_ctx = NULL,
    };
    httpd_uri_t post_uri = {
        .uri      = "/save",
        .method   = HTTP_POST,
        .handler  = portal_post_handler,
        .user_ctx = cfg,
    };

    httpd_register_uri_handler(server, &get_uri);
    httpd_register_uri_handler(server, &post_uri);

    ESP_LOGI(TAG, "Captive portal running. Connect to SSID 'EdgePDM-Setup' and navigate to 192.168.4.1");

    /* Block until POST handler gives the semaphore (config saved). */
    /* Timeout: 10 minutes maximum before giving up. */
    xSemaphoreTake(s_portal_done_sem, pdMS_TO_TICKS(600000));

    httpd_stop(server);
    esp_wifi_stop();

    vSemaphoreDelete(s_portal_done_sem);
    s_portal_done_sem = NULL;

    ESP_LOGI(TAG, "Captive portal done");
    return ESP_OK;
}

/* -------------------------------------------------------------------------
 * Public: app_wifi_connect
 * ------------------------------------------------------------------------- */
esp_err_t app_wifi_connect(const config_t *cfg)
{
    ESP_LOGI(TAG, "Connecting to WiFi SSID: %s", cfg->wifi_ssid);

    esp_netif_create_default_wifi_sta();
    wifi_init_config_t init_cfg = WIFI_INIT_CONFIG_DEFAULT();
    esp_wifi_init(&init_cfg);

    wifi_config_t sta_cfg = {0};
    strncpy((char *)sta_cfg.sta.ssid,     cfg->wifi_ssid,     sizeof(sta_cfg.sta.ssid) - 1);
    strncpy((char *)sta_cfg.sta.password, cfg->wifi_password, sizeof(sta_cfg.sta.password) - 1);

    esp_wifi_set_mode(WIFI_MODE_STA);
    esp_wifi_set_config(WIFI_IF_STA, &sta_cfg);
    esp_err_t err = esp_wifi_start();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_wifi_start failed: %s", esp_err_to_name(err));
        return err;
    }
    err = esp_wifi_connect();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_wifi_connect failed: %s", esp_err_to_name(err));
    }
    return err;
}
