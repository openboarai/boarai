# WebUI Channel Extension

[中文版](README_zh.md) | English

## Overview

This extension provides a modern web-based user interface for the BoarAi system. It features a responsive design with Vue 3, CSS variables for theming, and a component-based architecture.

## Features

- **Modern UI**: Clean, responsive design with CSS variables
- **Vue 3 Framework**: Component-based architecture with Vue Router
- **Responsive Design**: Works on desktop, tablet, and mobile devices
- **Theme Support**: Light and dark themes with customizable colors
- **API Ready**: Pre-configured API endpoints for future integration
- **Static File Serving**: Serves CSS, JavaScript, and assets

## Pages

### 1. Dashboard
- System status overview
- Key metrics cards
- Quick action buttons
- Recent activity list

### 2. Sessions
- Chat interface
- Session management
- Message history
- File upload preview

### 3. Extensions
- Extension list with categories
- Enable/disable extensions
- Extension configuration
- Status monitoring

### 4. Data
- Data viewing in tables
- Filtering and sorting
- Export functionality (CSV, JSON)
- Data statistics

### 5. Settings
- Theme configuration
- System settings
- UI customization
- Configuration export/import

## Navigation

### Top Navigation Bar
- **Logo**: 🐗 BoarAi ❖ (click to go home)
- **Tabs**: Dashboard, Sessions, Extensions, Data, Settings
- **User Info**: 🔌 Root@Host (click for user menu)

### Bottom Status Bar
- **Connection Status**: WebSocket connection indicator
- **Kernel Version**: System version information
- **Latency**: Response time in milliseconds
- **Uptime**: System uptime counter
- **Memory Usage**: Current memory consumption
- **Active Connections**: Number of connected clients

## Responsive Design

### Desktop (> 1024px)
- Full navigation bar with all tabs
- Complete status bar with all information
- Sidebar navigation (when implemented)

### Tablet (768px - 1024px)
- Navigation tabs may show icons only
- Status bar shows essential information
- Collapsible sidebar

### Mobile (< 768px)
- Navigation bar hides tabs, shows logo and user
- Bottom navigation bar appears
- Status bar shows minimal information
- Touch-friendly interface

## API Endpoints

### System Status
```
GET /api/status
```
Returns system status information including CPU, memory, and connection stats.

### Messages
```
GET /api/messages?session_id=default&limit=50&offset=0
```
Get message history for a session.

```
POST /api/messages/send
{
  "session_id": "default",
  "content": "Hello",
  "type": "text"
}
```
Send a new message.

### Extensions
```
GET /api/extensions
```
Get list of all extensions with their status.

### Configuration
```
GET /api/config
```
Get current configuration including theme and layout settings.

## File Structure

```
webui/
├── static/
│   ├── css/
│   │   ├── variables.css    # CSS variables for theming
│   │   └── main.css         # Main stylesheet
│   ├── js/
│   │   ├── components/
│   │   │   ├── navbar.js    # Navigation bar component
│   │   │   └── statusbar.js # Status bar component
│   │   └── app.js           # Main Vue application
│   ├── assets/              # Static assets (images, fonts)
│   ├── index.html           # Main application page
│   └── test.html            # Component test page
├── extension.py             # Extension main file
├── extension.yaml           # Extension configuration
└── README.md                # This file
```

## Usage

### Accessing the WebUI

After starting the BoarAi server, access the WebUI at:

```
http://localhost:26620/
```

Or with a specific IP address:

```
http://<server-ip>:26620/
```

### Testing Components

Visit the component test page to see Navbar and Status Bar in action:

```
http://localhost:26620/test
```

### Development

#### Adding New Components

1. Create a new JavaScript file in `static/js/components/`
2. Define the component using Vue 3 composition API
3. Register it in `app.js`
4. Add routes in the router configuration

#### Customizing Themes

Edit `static/css/variables.css` to change:
- Color scheme
- Typography
- Spacing
- Border radius
- Shadows

#### Adding New Pages

1. Create a new Vue component
2. Add a route in `app.js`
3. Add a navigation tab in `navbar.js`
4. Add a bottom navigation item in `app.js`

## Configuration

The WebUI does **not** own a listen address. It mounts `/` and `/static/**` onto
the core server's single aiohttp app, so HTTP, `/api/*` and `/ws` all share one
port. Bind address and port are set on the server, not here:

```bash
# default: 0.0.0.0:26620 (LAN-reachable)
BOARAI_HOST=127.0.0.1 BOARAI_PORT=27000 python main.py start
```

Extension-level options (`extension.yaml` → `config_defaults`):

- `theme`: initial UI theme (default `"light"`; the browser remembers overrides)
- `language`: initial UI language (default `"en"`)

## Dependencies

- Vue 3 (loaded via CDN with local fallback)
- Vue Router 4 (loaded via CDN with local fallback)
- aiohttp (for HTTP server)

## Browser Support

- Chrome 80+
- Firefox 75+
- Safari 13+
- Edge 80+

## Future Enhancements

- WebSocket real-time communication
- User authentication
- Multi-language support
- Plugin system for custom components
- Mobile app (PWA)
- Offline support

## License

This extension is part of the BoarAi project and follows the same license.
