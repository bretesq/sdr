import { listCategories } from '~/server/utils/queries'

/**
 * The distinct category strings, independent of any active filter.
 *
 * TalkgroupBrowser used to derive its category dropdown from the rows it had
 * loaded. That worked while it held all 4,163; with server-side filtering the
 * loaded set shrinks, and deriving from it would leave the dropdown offering
 * only the category you had already selected.
 */
export default defineEventHandler(() => {
  return { success: true, data: listCategories() }
})
