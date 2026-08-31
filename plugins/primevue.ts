import PrimeVue from 'primevue/config'
import Aura from '@primevue/themes/aura'

import Button from 'primevue/button'
import Checkbox from 'primevue/checkbox'
import Column from 'primevue/column'
import DataTable from 'primevue/datatable'
import Dialog from 'primevue/dialog'
import InputNumber from 'primevue/inputnumber'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import ProgressSpinner from 'primevue/progressspinner'
import Select from 'primevue/select'
import SelectButton from 'primevue/selectbutton'
import Tag from 'primevue/tag'

export default defineNuxtPlugin((nuxtApp) => {
  nuxtApp.vueApp.use(PrimeVue, {
    theme: { preset: Aura },
  })

  nuxtApp.vueApp.component('Button', Button)
  nuxtApp.vueApp.component('Checkbox', Checkbox)
  nuxtApp.vueApp.component('Column', Column)
  nuxtApp.vueApp.component('DataTable', DataTable)
  nuxtApp.vueApp.component('Dialog', Dialog)
  nuxtApp.vueApp.component('InputNumber', InputNumber)
  nuxtApp.vueApp.component('InputText', InputText)
  nuxtApp.vueApp.component('Message', Message)
  nuxtApp.vueApp.component('ProgressSpinner', ProgressSpinner)
  nuxtApp.vueApp.component('Select', Select)
  nuxtApp.vueApp.component('SelectButton', SelectButton)
  nuxtApp.vueApp.component('Tag', Tag)
})
