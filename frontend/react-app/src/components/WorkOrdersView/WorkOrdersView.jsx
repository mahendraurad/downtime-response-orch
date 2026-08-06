import React, { useContext } from 'react';
import { AppContext } from '../../context/AppContext';
import WorkOrderList from './WorkOrderList';
import WorkOrderDetail from './WorkOrderDetail';

export default function WorkOrdersView() {
  const { workOrders, selectedWO, setSelectedWO } = useContext(AppContext);

  return (
    <>
      <WorkOrderList workOrders={workOrders} selectedWO={selectedWO} onSelect={setSelectedWO} />
      <div className="wod">
        <WorkOrderDetail wo={selectedWO} />
      </div>
    </>
  );
}
