<?php
/**
 * Plugin Name: PetDesk QA Connector
 * Plugin URI: https://github.com/jonathanpimperton/zero-touch-qa
 * Description: Exposes site health data to PetDesk's Zero-Touch QA Scanner via a secure API endpoint.
 * Version: 1.1.0
 * Author: PetDesk
 * Author URI: https://petdesk.com
 * License: Proprietary
 * Update URI: https://github.com/jonathanpimperton/zero-touch-qa
 */

// Prevent direct access
if (!defined('ABSPATH')) {
    exit;
}

// Plugin constants
define('PETDESK_QA_VERSION', '1.1.0');
define('PETDESK_QA_PLUGIN_FILE', __FILE__);
define('PETDESK_QA_PLUGIN_SLUG', 'petdesk-qa-connector');

// GitHub repository for auto-updates
define('PETDESK_QA_GITHUB_REPO', 'jonathanpimperton/zero-touch-qa');

// API Key - Change this to a secure random string in production
// This same key must be set in the QA Scanner's environment variables
define('PETDESK_QA_API_KEY', 'petdesk-qa-2026-hackathon-key');

// =============================================================================
// GITHUB AUTO-UPDATE SYSTEM
// =============================================================================

/**
 * Check GitHub for plugin updates
 */
add_filter('pre_set_site_transient_update_plugins', 'petdesk_qa_check_for_updates');
function petdesk_qa_check_for_updates($transient) {
    if (empty($transient->checked)) {
        return $transient;
    }

    // Get the plugin file path relative to plugins directory
    $plugin_file = plugin_basename(PETDESK_QA_PLUGIN_FILE);

    // Get current version
    $current_version = PETDESK_QA_VERSION;

    // Check GitHub for latest release
    $github_response = petdesk_qa_get_github_release();

    if ($github_response && isset($github_response['tag_name'])) {
        $latest_version = ltrim($github_response['tag_name'], 'v');

        // Compare versions
        if (version_compare($latest_version, $current_version, '>')) {
            // Find the zip asset in the release
            $download_url = '';
            if (isset($github_response['assets']) && is_array($github_response['assets'])) {
                foreach ($github_response['assets'] as $asset) {
                    if (strpos($asset['name'], '.zip') !== false) {
                        $download_url = $asset['browser_download_url'];
                        break;
                    }
                }
            }

            // Fallback to zipball if no asset found
            if (empty($download_url) && isset($github_response['zipball_url'])) {
                $download_url = $github_response['zipball_url'];
            }

            if (!empty($download_url)) {
                $transient->response[$plugin_file] = (object) array(
                    'slug'        => PETDESK_QA_PLUGIN_SLUG,
                    'plugin'      => $plugin_file,
                    'new_version' => $latest_version,
                    'url'         => 'https://github.com/' . PETDESK_QA_GITHUB_REPO,
                    'package'     => $download_url,
                    'icons'       => array(),
                    'banners'     => array(),
                    'tested'      => get_bloginfo('version'),
                    'requires'    => '5.0',
                );
            }
        }
    }

    return $transient;
}

/**
 * Get latest release info from GitHub
 */
function petdesk_qa_get_github_release() {
    $cache_key = 'petdesk_qa_github_release';
    $cached = get_transient($cache_key);

    if ($cached !== false) {
        return $cached;
    }

    $url = 'https://api.github.com/repos/' . PETDESK_QA_GITHUB_REPO . '/releases/latest';

    $response = wp_remote_get($url, array(
        'timeout' => 10,
        'headers' => array(
            'Accept'     => 'application/vnd.github.v3+json',
            'User-Agent' => 'PetDesk-QA-Connector/' . PETDESK_QA_VERSION,
        ),
    ));

    if (is_wp_error($response) || wp_remote_retrieve_response_code($response) !== 200) {
        // Cache failure for 1 hour to avoid hammering GitHub
        set_transient($cache_key, array(), HOUR_IN_SECONDS);
        return false;
    }

    $body = json_decode(wp_remote_retrieve_body($response), true);

    // Cache for 6 hours
    set_transient($cache_key, $body, 6 * HOUR_IN_SECONDS);

    return $body;
}

/**
 * Show plugin details in the update popup
 */
add_filter('plugins_api', 'petdesk_qa_plugin_info', 20, 3);
function petdesk_qa_plugin_info($result, $action, $args) {
    if ($action !== 'plugin_information' || !isset($args->slug) || $args->slug !== PETDESK_QA_PLUGIN_SLUG) {
        return $result;
    }

    $github_response = petdesk_qa_get_github_release();

    if (!$github_response) {
        return $result;
    }

    $latest_version = ltrim($github_response['tag_name'], 'v');

    return (object) array(
        'name'              => 'PetDesk QA Connector',
        'slug'              => PETDESK_QA_PLUGIN_SLUG,
        'version'           => $latest_version,
        'author'            => '<a href="https://petdesk.com">PetDesk</a>',
        'homepage'          => 'https://github.com/' . PETDESK_QA_GITHUB_REPO,
        'requires'          => '5.0',
        'tested'            => get_bloginfo('version'),
        'downloaded'        => 0,
        'last_updated'      => isset($github_response['published_at']) ? $github_response['published_at'] : '',
        'sections'          => array(
            'description'   => 'Exposes site health data to PetDesk\'s Zero-Touch QA Scanner via a secure API endpoint.',
            'changelog'     => isset($github_response['body']) ? nl2br($github_response['body']) : 'See GitHub releases for changelog.',
        ),
        'download_link'     => isset($github_response['zipball_url']) ? $github_response['zipball_url'] : '',
    );
}

/**
 * Clear update cache when plugin is updated
 */
add_action('upgrader_process_complete', 'petdesk_qa_clear_update_cache', 10, 2);
function petdesk_qa_clear_update_cache($upgrader, $options) {
    if ($options['action'] === 'update' && $options['type'] === 'plugin') {
        delete_transient('petdesk_qa_github_release');
    }
}

/**
 * Register the REST API endpoint
 */
add_action('rest_api_init', function () {
    register_rest_route('petdesk-qa/v1', '/site-check', array(
        'methods'  => 'GET',
        'callback' => 'petdesk_qa_site_check',
        'permission_callback' => 'petdesk_qa_verify_api_key',
    ));
});

/**
 * Verify the API key from request header
 */
function petdesk_qa_verify_api_key($request) {
    $provided_key = $request->get_header('X-PetDesk-QA-Key');

    if (empty($provided_key)) {
        return new WP_Error(
            'missing_api_key',
            'API key is required. Include X-PetDesk-QA-Key header.',
            array('status' => 401)
        );
    }

    if (!hash_equals(PETDESK_QA_API_KEY, $provided_key)) {
        return new WP_Error(
            'invalid_api_key',
            'Invalid API key.',
            array('status' => 403)
        );
    }

    return true;
}

/**
 * Main endpoint handler - returns all site check data
 */
function petdesk_qa_site_check($request) {
    return rest_ensure_response(array(
        'success'   => true,
        'timestamp' => current_time('c'),
        'site_url'  => get_site_url(),
        'data'      => array(
            'wordpress'    => petdesk_qa_get_wp_info(),
            'plugins'      => petdesk_qa_get_plugins_info(),
            'themes'       => petdesk_qa_get_themes_info(),
            'settings'     => petdesk_qa_get_settings_info(),
            'forms'        => petdesk_qa_get_forms_info(),
            'media'        => petdesk_qa_get_media_info(),
        ),
    ));
}

/**
 * Get WordPress core info
 */
function petdesk_qa_get_wp_info() {
    global $wp_version;

    $update_info = get_site_transient('update_core');
    $update_available = false;
    $latest_version = $wp_version;

    if (isset($update_info->updates) && !empty($update_info->updates)) {
        $latest = $update_info->updates[0];
        if (isset($latest->version) && version_compare($latest->version, $wp_version, '>')) {
            $update_available = true;
            $latest_version = $latest->version;
        }
    }

    return array(
        'version'          => $wp_version,
        'update_available' => $update_available,
        'latest_version'   => $latest_version,
    );
}

/**
 * Get plugins info with update status
 */
function petdesk_qa_get_plugins_info() {
    if (!function_exists('get_plugins')) {
        require_once ABSPATH . 'wp-admin/includes/plugin.php';
    }

    $all_plugins = get_plugins();
    $active_plugins = get_option('active_plugins', array());
    $update_info = get_site_transient('update_plugins');

    $plugins = array();

    foreach ($all_plugins as $plugin_file => $plugin_data) {
        $update_available = false;
        $new_version = null;

        if (isset($update_info->response[$plugin_file])) {
            $update_available = true;
            $new_version = $update_info->response[$plugin_file]->new_version;
        }

        $plugins[] = array(
            'name'             => $plugin_data['Name'],
            'version'          => $plugin_data['Version'],
            'active'           => in_array($plugin_file, $active_plugins),
            'update_available' => $update_available,
            'new_version'      => $new_version,
        );
    }

    return $plugins;
}

/**
 * Get themes info with update status
 */
function petdesk_qa_get_themes_info() {
    $all_themes = wp_get_themes();
    $active_theme = get_stylesheet();
    $update_info = get_site_transient('update_themes');

    $themes = array();

    foreach ($all_themes as $theme_slug => $theme) {
        $update_available = false;
        $new_version = null;

        if (isset($update_info->response[$theme_slug])) {
            $update_available = true;
            $new_version = $update_info->response[$theme_slug]['new_version'];
        }

        $themes[] = array(
            'name'             => $theme->get('Name'),
            'version'          => $theme->get('Version'),
            'active'           => ($theme_slug === $active_theme),
            'update_available' => $update_available,
            'new_version'      => $new_version,
        );
    }

    return $themes;
}

/**
 * Get relevant WordPress settings
 */
function petdesk_qa_get_settings_info() {
    return array(
        'timezone_string' => get_option('timezone_string', ''),
        'gmt_offset'      => get_option('gmt_offset', 0),
        'date_format'     => get_option('date_format'),
        'time_format'     => get_option('time_format'),
        'admin_email'     => get_option('admin_email'),
        'blogname'        => get_option('blogname'),
        'siteurl'         => get_option('siteurl'),
        'home'            => get_option('home'),
    );
}

/**
 * Get forms info - supports Gravity Forms and WPForms
 */
function petdesk_qa_get_forms_info() {
    $forms_data = array(
        'gravity_forms_active' => false,
        'wpforms_active'       => false,
        'form_plugin'          => 'none',
        'forms'                => array(),
    );

    // Check for Gravity Forms first
    if (class_exists('GFAPI')) {
        $forms_data['gravity_forms_active'] = true;
        $forms_data['form_plugin'] = 'gravity_forms';

        $forms = GFAPI::get_forms();

        foreach ($forms as $form) {
            $form_info = array(
                'id'            => $form['id'],
                'title'         => $form['title'],
                'is_active'     => (bool) $form['is_active'],
                'source'        => 'gravity_forms',
                'notifications' => array(),
            );

            // Get notifications for this form
            if (isset($form['notifications']) && is_array($form['notifications'])) {
                foreach ($form['notifications'] as $notif_id => $notification) {
                    $form_info['notifications'][] = array(
                        'name'      => isset($notification['name']) ? $notification['name'] : 'Unnamed',
                        'to'        => isset($notification['to']) ? $notification['to'] : '',
                        'is_active' => isset($notification['isActive']) ? (bool) $notification['isActive'] : true,
                        'event'     => isset($notification['event']) ? $notification['event'] : '',
                    );
                }
            }

            $forms_data['forms'][] = $form_info;
        }

        return $forms_data;
    }

    // Check for WPForms
    if (function_exists('wpforms') || class_exists('WPForms')) {
        $forms_data['wpforms_active'] = true;
        $forms_data['form_plugin'] = 'wpforms';

        // WPForms stores forms as custom post type 'wpforms'
        $wpforms_posts = get_posts(array(
            'post_type'      => 'wpforms',
            'posts_per_page' => 50,
            'post_status'    => 'publish',
        ));

        foreach ($wpforms_posts as $form_post) {
            // Form data is stored as JSON in post_content
            $form_data = json_decode($form_post->post_content, true);

            if (!$form_data) {
                continue;
            }

            $form_info = array(
                'id'            => $form_post->ID,
                'title'         => isset($form_data['settings']['form_title']) ? $form_data['settings']['form_title'] : $form_post->post_title,
                'is_active'     => true,
                'source'        => 'wpforms',
                'notifications' => array(),
            );

            // Extract notification settings
            if (isset($form_data['settings']['notifications']) && is_array($form_data['settings']['notifications'])) {
                foreach ($form_data['settings']['notifications'] as $notif_id => $notification) {
                    // WPForms notification structure
                    $to_email = '';
                    if (isset($notification['email'])) {
                        $to_email = $notification['email'];
                    } elseif (isset($notification['carboncopy'])) {
                        $to_email = $notification['carboncopy'];
                    }

                    // Check if it uses admin email placeholder
                    if ($to_email === '{admin_email}') {
                        $to_email = get_option('admin_email') . ' (admin_email)';
                    }

                    $form_info['notifications'][] = array(
                        'name'      => isset($notification['notification_name']) ? $notification['notification_name'] : 'Notification ' . ($notif_id + 1),
                        'to'        => $to_email,
                        'is_active' => !isset($notification['notification_disable']) || $notification['notification_disable'] !== '1',
                        'event'     => 'form_submission',
                    );
                }
            }

            // If no notifications found, check for legacy single notification format
            if (empty($form_info['notifications']) && isset($form_data['settings']['notification_enable']) && $form_data['settings']['notification_enable'] === '1') {
                $to_email = isset($form_data['settings']['notification_email']) ? $form_data['settings']['notification_email'] : '';
                if ($to_email === '{admin_email}') {
                    $to_email = get_option('admin_email') . ' (admin_email)';
                }
                $form_info['notifications'][] = array(
                    'name'      => 'Default Notification',
                    'to'        => $to_email,
                    'is_active' => true,
                    'event'     => 'form_submission',
                );
            }

            $forms_data['forms'][] = $form_info;
        }

        return $forms_data;
    }

    // No supported form plugin found
    return $forms_data;
}

/**
 * Get media library summary (check for old/template files)
 */
function petdesk_qa_get_media_info() {
    $media_info = array(
        'total_count'      => 0,
        'template_files'   => array(),
        'old_files'        => array(),
    );

    // Template filename patterns to flag
    $template_patterns = array(
        'whiskerframe', 'placeholder', 'sample', 'demo',
        'test-', 'test_', 'dummy', 'lorem', 'default-', 'default_'
    );

    // Get media items
    $args = array(
        'post_type'      => 'attachment',
        'posts_per_page' => 200,
        'post_status'    => 'inherit',
    );

    $media_query = new WP_Query($args);
    $media_info['total_count'] = $media_query->found_posts;

    // Check for old files (older than 1 year) - threshold for flagging
    $one_year_ago = strtotime('-365 days');

    foreach ($media_query->posts as $media) {
        $filename = basename(get_attached_file($media->ID));
        $filename_lower = strtolower($filename);
        $upload_date = strtotime($media->post_date);

        // Check for template filenames
        foreach ($template_patterns as $pattern) {
            if (strpos($filename_lower, $pattern) !== false) {
                $media_info['template_files'][] = array(
                    'filename' => $filename,
                    'url'      => wp_get_attachment_url($media->ID),
                    'date'     => $media->post_date,
                    'pattern'  => $pattern,
                );
                break;
            }
        }

        // Check for old files
        if ($upload_date < $one_year_ago) {
            $media_info['old_files'][] = array(
                'filename' => $filename,
                'url'      => wp_get_attachment_url($media->ID),
                'date'     => $media->post_date,
            );
        }
    }

    // Limit to first 20 of each to avoid huge responses
    $media_info['template_files'] = array_slice($media_info['template_files'], 0, 20);
    $media_info['old_files'] = array_slice($media_info['old_files'], 0, 20);

    return $media_info;
}
