import { createApp } from 'vue'
import App from './App.vue'
import vuetify from './plugins/vuetify'
import { loadFonts } from './plugins/webfontloader'
import primaryModifierLongPress from './utils/primaryModifierLongPress'

loadFonts()

createApp(App)
  .use(vuetify)
  .directive('primary-modifier-long-press', primaryModifierLongPress)
  .mount('#app')
