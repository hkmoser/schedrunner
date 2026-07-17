import WidgetKit
import SwiftUI

/// Entry point for the widget extension: the Live Activity plus the three Home Screen widgets.
@main
struct DashboardWidgetBundle: WidgetBundle {
    var body: some Widget {
        DashboardLiveActivity()
        ReposWidget()
        ActivityWidget()
        BalancesWidget()
        DeployWidget()
        ServerHealthWidget()
        SingleRepoWidget()
        SingleLogWidget()
    }
}
