/** @odoo-module **/

import { registry } from "@web/core/registry";
import { formView } from "@web/views/form/form_view";
import { FormController } from "@web/views/form/form_controller";
import { useService } from "@web/core/utils/hooks";
import { useSubEnv } from "@odoo/owl";

export class InvoiceExtractController extends FormController {
    setup() {
        super.setup();
        this.ui = useService("ui");

        // Odoo 16+ intercepts button clicks via environment
        const originalOnClickViewButton = this.env.onClickViewButton;

        useSubEnv({
            onClickViewButton: async (params) => {
                const actionName = params.clickParams && params.clickParams.name;
                const isExtract = actionName === 'action_extract_invoice';

                if (isExtract) {
                    this.ui.block();
                    document.body.classList.add('o_invoice_extract_processing');
                }

                try {
                    if (originalOnClickViewButton) {
                        return await originalOnClickViewButton(params);
                    }
                } finally {
                    if (isExtract) {
                        this.ui.unblock();
                        document.body.classList.remove('o_invoice_extract_processing');
                    }
                }
            }
        });
    }
}

registry.category("views").add("test_model_form", {
    ...formView,
    Controller: InvoiceExtractController,
});
