import React, { useContext } from 'react';
import { AppContext } from '../../context/AppContext';
import WorkOrderList from './WorkOrderList';
import WorkOrderDetail from './WorkOrderDetail';
import WorkOrderAI from './WorkOrderAI';

export default function WorkOrdersView() {
  const { selectedWO, setSelectedWO } = useContext(AppContext);

  return (
    <>
      <WorkOrderList selectedWO={selectedWO} onSelect={setSelectedWO} />
      <div className="wod">
        <WorkOrderDetail wo={selectedWO} />
        <WorkOrderAI wo={selectedWO} />
      </div>
    </>
  );
}
