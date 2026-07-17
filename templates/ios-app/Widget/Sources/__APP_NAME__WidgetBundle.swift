import WidgetKit
import SwiftUI

/// Entry point for the widget extension: the Live Activity plus the three Home Screen widgets.
@main
struct __APP_NAME__WidgetBundle: WidgetBundle {
    var body: some Widget {
        __APP_NAME__LiveActivity()
        ReposWidget()
        ActivityWidget()
        BalancesWidget()
        DeployWidget()
        ServerHealthWidget()
        SingleRepoWidget()
        SingleLogWidget()
    }
}
