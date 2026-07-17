import type { JSONValue } from "./manifest";

export interface FormItem {
  key: string;
  value: JSONValue;
  type: string;
  category?: string;
}

// Collects editable field values for a page so a `submit` action can POST them.
// New-row controls use reserved keys (__new_key/__new_value/__new_type) which are
// assembled into a single new item on submit.
export class FormState {
  private entries = new Map<string, { value: JSONValue; type: string }>();

  set(key: string, value: JSONValue, type = "string") {
    this.entries.set(key, { value, type });
  }

  get(key: string): JSONValue | undefined {
    return this.entries.get(key)?.value;
  }

  toItems(): FormItem[] {
    const items: FormItem[] = [];
    let newKey: JSONValue | undefined;
    let newValue: JSONValue | undefined;
    let newType: JSONValue | undefined;
    let newCategory: JSONValue | undefined;
    for (const [key, { value, type }] of this.entries) {
      if (key === "__new_key") newKey = value;
      else if (key === "__new_value") newValue = value;
      else if (key === "__new_type") newType = value;
      else if (key === "__new_category") newCategory = value;
      else items.push({ key, value, type });
    }
    if (typeof newKey === "string" && newKey.trim()) {
      const item: FormItem = {
        key: newKey.trim(),
        value: newValue ?? "",
        type: typeof newType === "string" && newType ? newType : "string",
      };
      if (typeof newCategory === "string" && newCategory.trim()) item.category = newCategory.trim();
      items.push(item);
    }
    return items;
  }
}
